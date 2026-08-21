"""CommentEditor — the in-house draw/comment editor, revived 2026-08-20
against the user's own CommentEditor.ui (previously extracted to the
separate BananaSketch plugin on 2026-08-08; see draw_overlay.py's own
revival note and git history at commit f9b3505 in UkoreHubDev for the
original EditVideoDialog this replaces). Wraps an edit-mode PlayerWidget
(show_edit_tools=True — DrawOverlay + toolbar, see player_widget.py) plus a
keyframe comment table. Everything here operates on sequence_dir (an
already-extracted image sequence), never a video path directly —
video_library_page.py's _on_edit_comment_clicked calls ensure_sequence
before ever constructing this dialog.

Save Comment / Cancel Comment are dialog-level batch commit/discard —
confirmed with the user this round, a real change from the old system's
save-immediately-on-every-signal behavior (those buttons wouldn't otherwise
make sense): every stroke/comment edit only mutates self._frames in memory
while the dialog is open. Cancel simply closes without ever calling
comment_store.save — the on-disk file is untouched. Save writes
self._frames back via comment_store.save, then — only if this video is
already shared — pushes just the updated comments.json to the cloud via
CommentSyncWorker (the incremental-sync-on-save behavior confirmed this
round, distinct from Mark as Share's one-time full upload).

**Table redesigned 2026-08-21** per the user's own request: row selection
(clicking any row snaps the player to that frame, not just double-clicking
the Comment cell — see _on_table_row_selected), Time column dropped,
Author moved to the last column, and a frame with strokes but no text
comment yet now still gets its own row (previously only frames with an
actual comment appeared at all — see _refresh_table). Previous/Next
Comment buttons (also Shift+A/Shift+D) jump between keyframes the same
list drives."""

from __future__ import annotations

import datetime
import logging
import uuid
from pathlib import Path

from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ukoreshot_plugin.core import comment_store
from ukoreshot_plugin.core.share_sync import CommentSyncWorker
from ukoreshot_plugin.interface.draw_overlay import Stroke
from ukoreshot_plugin.interface.player_widget import PlayerWidget

_logger = logging.getLogger("UkoreShot.CommentEditor")

_UI_FILE = Path(__file__).resolve().parent / "CommentEditor.ui"
_COL_FRAME, _COL_COMMENT, _COL_AUTHOR = range(3)


class CommentEditor(QDialog):
    def __init__(self, sequence_dir: Path, *, api, project_id: str | None, repo_id: str | None, parent=None):
        super().__init__(parent)
        _logger.info("CommentEditor.__init__ starting for %s", sequence_dir)
        self.setWindowTitle("Comment - {}".format(sequence_dir.name))
        self.setWindowState(Qt.WindowMaximized)

        self._sequence_dir = sequence_dir
        self._api = api
        self._project_id = project_id
        self._repo_id = repo_id
        self._sync_worker: CommentSyncWorker | None = None
        self._current_frame_index = 0
        self._suppress_selection_jump = False

        data = comment_store.load(sequence_dir)
        # Working copy only — never written back except by Save (see the
        # module docstring's batch-commit note). Cancel just discards this.
        self._frames: dict = data["frames"]

        loader = QUiLoader()
        ui_file = QFile(str(_UI_FILE))
        if not ui_file.open(QFile.ReadOnly):
            _logger.error("could not open %s for reading (errorString=%s)", _UI_FILE, ui_file.errorString())
        self.ui = loader.load(ui_file, self)
        ui_file.close()
        if self.ui is None:
            _logger.error("QUiLoader.load() returned None for %s — loader.errorString()=%s", _UI_FILE, loader.errorString())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.ui)

        find = self.ui.findChild
        self.viewer_group: QGroupBox = find(QGroupBox, "groupBox_playblast_viewer")
        self.comment_group: QGroupBox = find(QGroupBox, "groupBox")
        self.table: QTableWidget = find(QTableWidget, "tableWidget")
        self.save_button: QPushButton = find(QPushButton, "pushButton_save_comment")
        # objectName is "pushButton_2" in CommentEditor.ui, not
        # "pushButton_cancel_comment" — a Qt Designer auto-generated name
        # (the button was likely re-added/re-dragged in Designer, resetting
        # it) rather than something to rely on staying stable; matched to
        # whatever the .ui file currently has, same as every other find()
        # call here, rather than assuming a descriptive name that isn't
        # actually there.
        self.cancel_button: QPushButton = find(QPushButton, "pushButton_2")

        for _name, _widget in [
            ("groupBox_playblast_viewer", self.viewer_group),
            ("groupBox", self.comment_group),
            ("tableWidget", self.table),
            ("pushButton_save_comment", self.save_button),
            ("pushButton_2", self.cancel_button),
        ]:
            if _widget is None:
                _logger.error("CommentEditor.ui has no widget named %r — findChild returned None", _name)

        self.player_widget = PlayerWidget(show_edit_tools=True)
        self.player_widget.frameIndexChanged.connect(self._on_frame_index_changed)
        self.player_widget.draw_overlay.strokesChanged.connect(self._on_strokes_changed)
        viewer_layout = QVBoxLayout(self.viewer_group)
        viewer_layout.setContentsMargins(4, 16, 4, 4)
        viewer_layout.addWidget(self.player_widget)

        # Previous/Next Comment — added 2026-08-21, code-built (no matching
        # widgets in CommentEditor.ui yet) into the "Keyframe Comment"
        # groupbox's own layout, right below the table.
        self.prev_comment_button = QPushButton("< Previous")
        self.next_comment_button = QPushButton("Next >")
        self.prev_comment_button.clicked.connect(self._on_prev_comment_clicked)
        self.next_comment_button.clicked.connect(self._on_next_comment_clicked)
        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_comment_button)
        nav_row.addWidget(self.next_comment_button)
        if self.comment_group is not None and self.comment_group.layout() is not None:
            self.comment_group.layout().addLayout(nav_row, 1, 0)
        self._prev_comment_shortcut = QShortcut(QKeySequence("Shift+A"), self)
        self._prev_comment_shortcut.activated.connect(self._on_prev_comment_clicked)
        self._next_comment_shortcut = QShortcut(QKeySequence("Shift+D"), self)
        self._next_comment_shortcut.activated.connect(self._on_next_comment_clicked)

        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Frame", "Comment", "Author"])
        self.table.setEditTriggers(QTableWidget.DoubleClicked)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.itemSelectionChanged.connect(self._on_table_row_selected)
        self._suppress_item_changed = False

        self.save_button.clicked.connect(self._on_save_clicked)
        self.cancel_button.clicked.connect(self.reject)

        self.player_widget.load_sequence(sequence_dir)
        self._refresh_table()
        _logger.info("CommentEditor.__init__ finished (%d frame(s) with saved data)", len(self._frames))

    # -- frame navigation / drawing persistence (in-memory only) -----------

    def _on_frame_index_changed(self, frame_index: int) -> None:
        self._current_frame_index = frame_index
        entry = self._frames.get(str(frame_index), {})
        strokes = [Stroke.from_dict(s) for s in entry.get("strokes", [])]
        self.player_widget.draw_overlay.load_frame(strokes, entry.get("text_boxes", []))
        self._refresh_table()

    def _on_strokes_changed(self) -> None:
        self._record_frame_change(self._current_frame_index, strokes=self.player_widget.draw_overlay.current_strokes())

    def _record_frame_change(self, frame_index: int, *, strokes=None, comments=None) -> None:
        key = str(frame_index)
        entry = dict(self._frames.get(key, {}))
        if strokes is not None:
            serialized = [s.to_dict() for s in strokes]
            if serialized:
                entry["strokes"] = serialized
            else:
                entry.pop("strokes", None)
        if comments is not None:
            if comments:
                entry["comments"] = comments
            else:
                entry.pop("comments", None)
        if entry:
            self._frames[key] = entry
        else:
            self._frames.pop(key, None)
        # A stroke edit changes whether this frame has its own row at all
        # (see _refresh_table's "strokes but no comment yet" case) just as
        # much as a comment edit does, so both refresh the list — confirmed
        # with the user this round ("รายการก็จะงอกเพิ่ม" — the list should
        # grow when there's drawing on a frame too, not just a comment).
        self._refresh_table()

    # -- keyframe comment table ---------------------------------------------

    def _keyframe_indices(self) -> list[int]:
        """Every frame with something saved (a comment, or just strokes) —
        drives both the table's row set and Previous/Next Comment
        navigation, so the two always agree on what counts as a
        "keyframe"."""
        indices = []
        for key, entry in self._frames.items():
            if entry.get("comments") or entry.get("strokes"):
                indices.append(int(key))
        return sorted(indices)

    def _refresh_table(self) -> None:
        rows: list[tuple[int, dict | None]] = []
        for frame_index in self._keyframe_indices():
            entry = self._frames.get(str(frame_index), {})
            comments = entry.get("comments", [])
            if comments:
                # Always listed, even for the current frame — the trailing
                # composer row below is *additional* (a way to add one
                # more comment to whatever's on screen now), never a
                # replacement for comments a frame already has. Fixed
                # 2026-08-21 after a real bug: the current frame's own
                # comments were vanishing from the table the moment it
                # became current, since the composer row used to stand in
                # for it unconditionally.
                for comment in comments:
                    rows.append((frame_index, comment))
            elif frame_index != self._current_frame_index:
                # Has strokes but no text comment yet, and isn't the frame
                # on screen right now — still shows up, per the user's own
                # request, as a blank row you can type into. The current
                # frame's own blank case is covered by the trailing
                # composer row instead, so it isn't duplicated here.
                rows.append((frame_index, None))

        self._suppress_item_changed = True
        self._suppress_selection_jump = True
        self.table.setRowCount(len(rows) + 1)
        for row, (frame_index, comment) in enumerate(rows):
            self._set_row(row, frame_index, comment)
        # Trailing composer row, always for whichever frame the player is
        # currently on — double-click its Comment cell to add a(nother)
        # comment there, even if that frame already has one or more.
        self._set_row(len(rows), self._current_frame_index, None)
        self._suppress_item_changed = False
        self._suppress_selection_jump = False

    def _set_row(self, row: int, frame_index: int, comment: dict | None) -> None:
        frame_item = QTableWidgetItem(str(frame_index))
        frame_item.setData(Qt.UserRole + 1, frame_index)
        frame_item.setFlags(frame_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, _COL_FRAME, frame_item)

        comment_item = QTableWidgetItem(comment.get("text", "") if comment else "")
        comment_item.setData(Qt.UserRole, comment.get("id") if comment else None)
        comment_item.setData(Qt.UserRole + 1, frame_index)
        self.table.setItem(row, _COL_COMMENT, comment_item)

        author_item = QTableWidgetItem(comment.get("author", "") if comment else "")
        author_item.setFlags(author_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, _COL_AUTHOR, author_item)

    def _on_table_row_selected(self) -> None:
        """Selecting any row (a single click anywhere in it, thanks to
        SelectRows — not just double-clicking the Comment cell) snaps the
        player to that row's frame — added 2026-08-21 per the user's own
        request. Guarded during _refresh_table's own rebuild so repopulating
        the table (which clears/re-lays-out selection) can't itself trigger
        a jump."""
        if self._suppress_selection_jump:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, _COL_FRAME)
        if item is None:
            return
        frame_index = item.data(Qt.UserRole + 1)
        if frame_index is not None and frame_index != self._current_frame_index:
            self.player_widget.jump_to_frame(frame_index)

    def _on_prev_comment_clicked(self) -> None:
        indices = self._keyframe_indices()
        earlier = [i for i in indices if i < self._current_frame_index]
        if earlier:
            self.player_widget.jump_to_frame(earlier[-1])

    def _on_next_comment_clicked(self) -> None:
        indices = self._keyframe_indices()
        later = [i for i in indices if i > self._current_frame_index]
        if later:
            self.player_widget.jump_to_frame(later[0])

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._suppress_item_changed or item.column() != _COL_COMMENT:
            return
        text = item.text().strip()
        comment_id = item.data(Qt.UserRole)
        frame_index = item.data(Qt.UserRole + 1)
        if frame_index is None:
            frame_index = self._current_frame_index

        if comment_id is None:
            if not text:
                return
            comments = list(self._frames.get(str(frame_index), {}).get("comments", []))
            comments.append(
                {
                    "id": _new_comment_id(),
                    "author": comment_store.current_username(self._api),
                    "text": text,
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            )
            self._record_frame_change(frame_index, comments=comments)
        else:
            comments = list(self._frames.get(str(frame_index), {}).get("comments", []))
            for comment in comments:
                if comment.get("id") == comment_id:
                    comment["text"] = text
                    break
            self._record_frame_change(frame_index, comments=comments)

    def _on_table_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        comment_item = self.table.item(item.row(), _COL_COMMENT)
        comment_id = comment_item.data(Qt.UserRole) if comment_item is not None else None
        if comment_id is None:
            return
        frame_index = comment_item.data(Qt.UserRole + 1)
        menu = QMenu(self)
        delete_action = menu.addAction("Delete Comment")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is delete_action:
            comments = [
                c for c in self._frames.get(str(frame_index), {}).get("comments", []) if c.get("id") != comment_id
            ]
            self._record_frame_change(frame_index, comments=comments)

    # -- save / cancel --------------------------------------------------------

    def _on_save_clicked(self) -> None:
        share_state = comment_store.get_share_state(self._sequence_dir)
        comment_store.save(self._sequence_dir, {"frames": self._frames, "share": share_state})
        _logger.info("saved comments.json for %s (%d frame(s))", self._sequence_dir, len(self._frames))
        if share_state.get("is_shared") and self._api.cloud_sync is not None and self._project_id and self._repo_id:
            _logger.info("video already shared — syncing comments.json to the cloud")
            self.save_button.setEnabled(False)
            worker = CommentSyncWorker(
                self._api.cloud_sync,
                project_id=self._project_id,
                repo_id=self._repo_id,
                sequence_dir=self._sequence_dir,
                parent=self,
            )
            worker.succeeded.connect(self._on_comment_sync_finished)
            worker.failed.connect(self._on_comment_sync_failed)
            worker.finished.connect(worker.deleteLater)
            self._sync_worker = worker
            worker.start()
            return
        self.accept()

    def _on_comment_sync_finished(self) -> None:
        self._sync_worker = None
        _logger.info("comments.json cloud sync succeeded")
        self.accept()

    def _on_comment_sync_failed(self, message: str) -> None:
        self._sync_worker = None
        _logger.warning("comments.json cloud sync failed: %s", message)
        QMessageBox.warning(self, "Cloud Sync Failed", "Comments saved locally, but the cloud sync failed: " + message)
        self.accept()


def _new_comment_id() -> str:
    return uuid.uuid4().hex[:8]

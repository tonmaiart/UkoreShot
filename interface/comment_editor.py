"""CommentEditor — the in-house draw/comment editor, revived 2026-08-20
against the user's own CommentEditor.ui (previously extracted to the
separate BananaSketch plugin on 2026-08-08; see draw_overlay.py's own
revival note and git history at commit f9b3505 in UkoreHubDev for the
original EditVideoDialog this replaces). Wraps an edit-mode PlayerWidget
(show_edit_tools=True — DrawOverlay, see player_widget.py) plus a
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
the Comment cell — see _on_table_row_selected) and a frame with strokes
but no text comment yet now still gets its own row (previously only
frames with an actual comment appeared at all — see _refresh_table).
Previous/Next Comment buttons (also Shift+A/Shift+D) jump between
keyframes the same list drives.

**Rewired onto CommentEditor.ui's own controls, 2026-08-21**: the .ui
already lays out pushButton_previous_frame/play/next_frame right beside
its own Previous/Next Comment buttons, and pushButton_undo/redo/
clear_frame as its own toolbar row — previously all dead/unused, since
this file built its own duplicate PlayerWidget-driven controls and
covered the .ui's own gridLayout_4 with a second, uninstalled layout of
its own (`QVBoxLayout(self.viewer_group)` on a QGroupBox that already had
a layout from the .ui). Now embeds player_widget into the .ui's own empty
verticalLayout_player placeholder instead, and wires the .ui's buttons
directly to PlayerWidget/DrawOverlay (see player_widget.py's own
show_edit_tools docstring note). The Author/username column is gone from
the Keyframe Comment table; a new leading icon column shows a green dot
on whichever row is the frame currently on screen.

**Brush color/speed/keyframe also moved onto the .ui, same day**:
player_widget.py's own code-built Select toggle/color swatch/size slider
toolbar is gone entirely (not replaced — brush size stays adjustable via
mouse wheel/"F"-drag over the canvas regardless, Select mode has no
remaining trigger); this dialog now owns brush-color picking itself via
pushButton_brush_color (_on_pick_brush_color, also wired to
draw_overlay.colorPickRequested for the existing middle-click-on-canvas
gesture). comboBox_speed (0.25x-1.00x) and lineEdit_keyframe (kept in
sync with the player, not just editable) replace player_widget.py's own
speed_slider/goto_frame_spin here too — PlayerWidget is now constructed
with own_transport_controls=False for exactly this reason."""

from __future__ import annotations

import bisect
import datetime
import logging
import uuid
from pathlib import Path

from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QLineEdit,
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
_COL_ACTIVE, _COL_FRAME, _COL_COMMENT = range(3)
_FRAME_COLUMN_WIDTH_FRACTION = 0.10
_ACTIVE_COLUMN_WIDTH = 28

# This plugin's own images/ folder — see player_widget.py's own _ICONS_DIR
# note for why (not the shared data/icons/ every other plugin uses).
_ICONS_DIR = Path(__file__).resolve().parents[1] / "images"
_PREV_FRAME_ICON_PATH = _ICONS_DIR / "icons8-chevron-left-26.png"
_NEXT_FRAME_ICON_PATH = _ICONS_DIR / "icons8-right-26.png"
_PLAY_ICON_PATH = _ICONS_DIR / "icons8-play-50.png"
_PAUSE_ICON_PATH = _ICONS_DIR / "icons8-pause-50.png"
_PREV_COMMENT_ICON_PATH = _ICONS_DIR / "prev_comment.png"
_NEXT_COMMENT_ICON_PATH = _ICONS_DIR / "next_comment.png"
_UNDO_ICON_PATH = _ICONS_DIR / "undo.png"
_REDO_ICON_PATH = _ICONS_DIR / "redo.png"
_CLEAR_FRAME_ICON_PATH = _ICONS_DIR / "clear_frame.png"

_active_frame_icon: QIcon | None = None


def _active_frame_dot_icon() -> QIcon:
    """A small filled green circle, drawn once and cached — marks whichever
    Keyframe Comment row is the frame currently on screen. Drawn
    programmatically rather than shipped as a PNG since it's just a solid
    dot, no need for a new asset file."""
    global _active_frame_icon
    if _active_frame_icon is None:
        size = 12
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2ecc71"))
        painter.drawEllipse(1, 1, size - 2, size - 2)
        painter.end()
        _active_frame_icon = QIcon(pixmap)
    return _active_frame_icon


class CommentEditor(QDialog):
    def __init__(self, sequence_dir: Path, *, api, project_id: str | None, repo_id: str | None, parent=None):
        super().__init__(parent)
        _logger.info("CommentEditor.__init__ starting for %s", sequence_dir)
        self.setWindowTitle("Comment - {}".format(sequence_dir.name))
        # A bare QDialog has no Maximize button by default — add it (and
        # Minimize, its usual pair) so the window can actually be maximized,
        # not just start that way. setWindowState(Maximized) is deliberately
        # NOT called here — fixed 2026-08-21 after a real crash:
        # WindowMaximized fires a synchronous resizeEvent() immediately,
        # and this class's own resizeEvent() override (below) touches
        # self.table, which doesn't exist yet this early in __init__ —
        # AttributeError, silently swallowed by Qt's C++/Python boundary
        # (PySide reports it as "Error calling Python override of
        # QDialog::resizeEvent()" and the dialog never finishes
        # constructing, which looked like "clicking Edit Comment does
        # nothing" from the outside). Maximizing now happens at the very
        # end of __init__ instead, once every widget it could touch exists.
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)

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
        self.prev_frame_button: QPushButton = find(QPushButton, "pushButton_previous_frame")
        self.play_button: QPushButton = find(QPushButton, "pushButton_play")
        self.next_frame_button: QPushButton = find(QPushButton, "pushButton_next_frame")
        self.prev_comment_button: QPushButton = find(QPushButton, "pushButton_previous_comment")
        self.next_comment_button: QPushButton = find(QPushButton, "pushButton_next_comment")
        self.undo_button: QPushButton = find(QPushButton, "pushButton_undo")
        self.redo_button: QPushButton = find(QPushButton, "pushButton_redo")
        self.clear_button: QPushButton = find(QPushButton, "pushButton_clear_frame")
        self.brush_color_button: QPushButton = find(QPushButton, "pushButton_brush_color")
        self.speed_combo: QComboBox = find(QComboBox, "comboBox_speed")
        self.keyframe_edit: QLineEdit = find(QLineEdit, "lineEdit_keyframe")
        self.viewer_player_layout: QVBoxLayout = find(QVBoxLayout, "verticalLayout_player")

        for _name, _widget in [
            ("groupBox_playblast_viewer", self.viewer_group),
            ("groupBox", self.comment_group),
            ("tableWidget", self.table),
            ("pushButton_save_comment", self.save_button),
            ("pushButton_2", self.cancel_button),
            ("pushButton_previous_frame", self.prev_frame_button),
            ("pushButton_play", self.play_button),
            ("pushButton_next_frame", self.next_frame_button),
            ("pushButton_previous_comment", self.prev_comment_button),
            ("pushButton_next_comment", self.next_comment_button),
            ("pushButton_undo", self.undo_button),
            ("pushButton_redo", self.redo_button),
            ("pushButton_clear_frame", self.clear_button),
            ("pushButton_brush_color", self.brush_color_button),
            ("comboBox_speed", self.speed_combo),
            ("lineEdit_keyframe", self.keyframe_edit),
            ("verticalLayout_player", self.viewer_player_layout),
        ]:
            if _widget is None:
                _logger.error("CommentEditor.ui has no widget named %r — findChild returned None", _name)

        # Embeds into the .ui's own empty verticalLayout_player placeholder
        # inside groupBox_playblast_viewer — not a fresh QVBoxLayout on the
        # groupbox itself, which already has its own gridLayout_4 (the
        # transport/toolbar rows below) from the .ui. own_transport_controls
        # =False: this .ui now supplies its own goto-frame (lineEdit_keyframe)
        # and speed (comboBox_speed) controls too, so PlayerWidget's own
        # code-built goto_frame_spin/speed_slider are skipped entirely.
        self.player_widget = PlayerWidget(show_edit_tools=True, own_transport_controls=False)
        self.player_widget.frameIndexChanged.connect(self._on_frame_index_changed)
        self.player_widget.draw_overlay.strokesChanged.connect(self._on_strokes_changed)
        self.player_widget.playingChanged.connect(self._on_playing_changed)
        if self.viewer_player_layout is not None:
            self.viewer_player_layout.addWidget(self.player_widget)

        # Transport + comment-nav + undo/redo/clear/brush-color/speed all
        # come from CommentEditor.ui now (see the module docstring's
        # 2026-08-21 note) instead of PlayerWidget building duplicate
        # widgets in code.
        PlayerWidget._set_button_icon(self.prev_frame_button, _PREV_FRAME_ICON_PATH, "<")
        PlayerWidget._set_button_icon(self.play_button, _PLAY_ICON_PATH, "Play")
        PlayerWidget._set_button_icon(self.next_frame_button, _NEXT_FRAME_ICON_PATH, ">")
        PlayerWidget._set_button_icon(self.prev_comment_button, _PREV_COMMENT_ICON_PATH, "< Comment")
        PlayerWidget._set_button_icon(self.next_comment_button, _NEXT_COMMENT_ICON_PATH, "Comment >")
        PlayerWidget._set_button_icon(self.undo_button, _UNDO_ICON_PATH, "Undo")
        PlayerWidget._set_button_icon(self.redo_button, _REDO_ICON_PATH, "Redo")
        PlayerWidget._set_button_icon(self.clear_button, _CLEAR_FRAME_ICON_PATH, "Clear")
        self.prev_frame_button.clicked.connect(lambda: self.player_widget.step_frame(-1))
        self.play_button.clicked.connect(self.player_widget.toggle_play)
        self.next_frame_button.clicked.connect(lambda: self.player_widget.step_frame(1))
        self.prev_comment_button.clicked.connect(self._on_prev_comment_clicked)
        self.next_comment_button.clicked.connect(self._on_next_comment_clicked)
        self.undo_button.clicked.connect(self.player_widget.draw_overlay.undo)
        self.redo_button.clicked.connect(self.player_widget.draw_overlay.redo)
        self.clear_button.clicked.connect(self.player_widget.draw_overlay.clear_frame)
        self._prev_comment_shortcut = QShortcut(QKeySequence("Shift+A"), self)
        self._prev_comment_shortcut.activated.connect(self._on_prev_comment_clicked)
        self._next_comment_shortcut = QShortcut(QKeySequence("Shift+D"), self)
        self._next_comment_shortcut.activated.connect(self._on_next_comment_clicked)

        # Brush color — moved here from player_widget.py's own now-removed
        # code-built color swatch (2026-08-21 per the user's own request):
        # this dialog owns the color state and opens QColorDialog itself,
        # same split every other .ui-driven control here uses. Also wired
        # to draw_overlay's own middle-click-on-canvas colorPickRequested
        # signal, same as the removed toolbar swatch was.
        self._brush_color = QColor("#ff3b30")
        self._update_brush_color_button()
        self.brush_color_button.clicked.connect(self._on_pick_brush_color)
        self.player_widget.draw_overlay.colorPickRequested.connect(self._on_pick_brush_color)
        self.player_widget.draw_overlay.set_color(self._brush_color)

        # Speed — 0.25x-1.00x discrete steps, replacing player_widget.py's
        # own now-removed speed_slider for this dialog.
        self.speed_combo.addItems(["0.25x", "0.5x", "0.75x", "1.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self._on_speed_combo_changed)

        # Keyframe number — replaces player_widget.py's own now-removed
        # goto_frame_spin. Kept in sync with the player (not just editable)
        # via _on_frame_index_changed below.
        self.keyframe_edit.setPlaceholderText("Frame")
        self.keyframe_edit.returnPressed.connect(self._on_keyframe_edit_entered)

        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["", "Frame", "Comment"])
        self.table.setEditTriggers(QTableWidget.DoubleClicked)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.itemSelectionChanged.connect(self._on_table_row_selected)
        self._suppress_item_changed = False

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_ACTIVE, QHeaderView.Fixed)
        header.setSectionResizeMode(_COL_FRAME, QHeaderView.Fixed)
        header.setSectionResizeMode(_COL_COMMENT, QHeaderView.Stretch)
        self.table.setColumnWidth(_COL_ACTIVE, _ACTIVE_COLUMN_WIDTH)
        self._apply_frame_column_width()

        self.save_button.clicked.connect(self._on_save_clicked)
        self.cancel_button.clicked.connect(self.reject)

        self.player_widget.load_sequence(sequence_dir)
        self._refresh_table()
        # Starts maximized — moved here, after everything above exists, see
        # the note by the window-flags line earlier in __init__.
        self.setWindowState(Qt.WindowMaximized)
        _logger.info("CommentEditor.__init__ finished (%d frame(s) with saved data)", len(self._frames))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_frame_column_width()

    def _apply_frame_column_width(self) -> None:
        # Guards against resizeEvent() firing before self.table exists —
        # see the __init__ note on why WindowMaximized moved to the end.
        # Kept as a second line of defense even with that fix in place,
        # since any future setWindowState/setGeometry call added earlier in
        # __init__ could reintroduce the exact same crash otherwise.
        table = getattr(self, "table", None)
        if table is None:
            return
        width = table.viewport().width()
        table.setColumnWidth(_COL_FRAME, max(32, int(width * _FRAME_COLUMN_WIDTH_FRACTION)))

    def _on_playing_changed(self, playing: bool) -> None:
        PlayerWidget._set_button_icon(self.play_button, _PAUSE_ICON_PATH if playing else _PLAY_ICON_PATH, "Pause" if playing else "Play")

    def _update_brush_color_button(self) -> None:
        self.brush_color_button.setStyleSheet(
            "background-color: {}; border: 1px solid #888;".format(self._brush_color.name())
        )

    def _on_pick_brush_color(self) -> None:
        color = QColorDialog.getColor(self._brush_color, self, "Brush Color")
        if color.isValid():
            self._brush_color = color
            self._update_brush_color_button()
            self.player_widget.draw_overlay.set_color(color)

    def _on_speed_combo_changed(self, text: str) -> None:
        try:
            rate = float(text.rstrip("x"))
        except ValueError:
            return
        self.player_widget.set_speed(rate)

    def _on_keyframe_edit_entered(self) -> None:
        try:
            frame_index = int(self.keyframe_edit.text().strip())
        except ValueError:
            return
        self.player_widget.jump_to_frame(frame_index)

    # -- frame navigation / drawing persistence (in-memory only) -----------

    def _on_frame_index_changed(self, frame_index: int) -> None:
        self._current_frame_index = frame_index
        self.keyframe_edit.setText(str(frame_index))
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

        # The composer row (always for whichever frame the player is
        # currently on) used to be appended unconditionally last — fixed
        # 2026-08-21 per the user's own request that the table always sort
        # by keyframe: scrubbing to an earlier frame than whatever already
        # had comments used to put its row at the very bottom instead of in
        # numeric order. bisect_right places it after any existing rows for
        # that same frame (reads as "the next new entry for this frame")
        # while still keeping the whole table in ascending frame order.
        insert_at = bisect.bisect_right([r[0] for r in rows], self._current_frame_index)
        rows.insert(insert_at, (self._current_frame_index, None))

        self._suppress_item_changed = True
        self._suppress_selection_jump = True
        self.table.setRowCount(len(rows))
        for row, (frame_index, comment) in enumerate(rows):
            self._set_row(row, frame_index, comment)
        self._suppress_item_changed = False
        self._suppress_selection_jump = False

    def _set_row(self, row: int, frame_index: int, comment: dict | None) -> None:
        active_item = QTableWidgetItem()
        active_item.setFlags(active_item.flags() & ~Qt.ItemIsEditable)
        if frame_index == self._current_frame_index:
            active_item.setIcon(_active_frame_dot_icon())
        self.table.setItem(row, _COL_ACTIVE, active_item)

        frame_item = QTableWidgetItem(str(frame_index))
        frame_item.setData(Qt.UserRole + 1, frame_index)
        frame_item.setFlags(frame_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, _COL_FRAME, frame_item)

        comment_item = QTableWidgetItem(comment.get("text", "") if comment else "")
        comment_item.setData(Qt.UserRole, comment.get("id") if comment else None)
        comment_item.setData(Qt.UserRole + 1, frame_index)
        self.table.setItem(row, _COL_COMMENT, comment_item)

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

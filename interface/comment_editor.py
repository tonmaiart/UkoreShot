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
with own_transport_controls=False for exactly this reason.

**Undo/redo is now a dialog-level, cross-frame action history (2026-08-21,
per the user's own request)** — not DrawOverlay's own per-frame stroke
undo anymore (that only ever tracked the *current* frame, reset on every
frame switch). `self._undo_stack`/`_redo_stack` hold `(frame_index,
pre-mutation snapshot)` pairs, pushed from the single point every draw/
erase/comment edit already funnels through (`_record_frame_change` —
see `_push_undo_snapshot`'s own docstring for why that one call site
covers exactly draw/erase/comment and nothing else). Undoing/redoing
restores the snapshot *and* jumps the player back to whichever frame the
action happened on (`_on_undo`/`_on_redo`/`_apply_frame_snapshot`).
Ctrl+Z/Ctrl+Shift+Z moved here from player_widget.py for the same reason.

**Ctrl+S added the same day**: a quick save that does *not* close the
dialog, unlike clicking pushButton_save_comment itself — see
`_save_comments(close_after=...)`, which both now share.

**tableWidget_comment (renamed from tableWidget) is read-only, same
day**: editing now goes through the new pushButton_edit_message
(`_on_edit_message_clicked`, a QInputDialog) instead of in-cell double-
click editing — `_apply_comment_text` is the shared add-or-edit logic
both that and the old inline-editing code path used."""

from __future__ import annotations

import bisect
import copy
import datetime
import logging
import uuid
from pathlib import Path

from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
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
_UNDO_STACK_LIMIT = 20

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
_EDIT_MESSAGE_ICON_PATH = _ICONS_DIR / "icons8-edit-50.png"

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
        # Global, cross-frame undo/redo — added 2026-08-21 per the user's
        # own request: each entry is (frame_index, pre-mutation snapshot of
        # self._frames[frame_index]), pushed once per draw/erase/comment
        # edit (see _push_undo_snapshot's own docstring for why
        # _record_frame_change is the one call site). Undoing/redoing
        # restores that snapshot and jumps the player back to frame_index —
        # a real behavior change from DrawOverlay's own former per-frame-
        # only undo stack, which never persisted across a frame switch.
        self._undo_stack: list[tuple[int, dict]] = []
        self._redo_stack: list[tuple[int, dict]] = []

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
        # Renamed from "tableWidget" to "tableWidget_comment" in the .ui
        # 2026-08-21 (the user's own change, to avoid confusion with other
        # tables elsewhere in the app) — findChild by the new name.
        self.table: QTableWidget = find(QTableWidget, "tableWidget_comment")
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
        # Added 2026-08-21 alongside the table rename — the table is now
        # read-only (see below); this is the only way to add/edit a
        # comment's text.
        self.edit_message_button: QPushButton = find(QPushButton, "pushButton_edit_message")
        self.viewer_player_layout: QVBoxLayout = find(QVBoxLayout, "verticalLayout_player")

        for _name, _widget in [
            ("groupBox_playblast_viewer", self.viewer_group),
            ("groupBox", self.comment_group),
            ("tableWidget_comment", self.table),
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
            ("pushButton_edit_message", self.edit_message_button),
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
        PlayerWidget._set_button_icon(self.edit_message_button, _EDIT_MESSAGE_ICON_PATH, "Edit")
        self.prev_frame_button.clicked.connect(lambda: self.player_widget.step_frame(-1))
        self.play_button.clicked.connect(self.player_widget.toggle_play)
        self.next_frame_button.clicked.connect(lambda: self.player_widget.step_frame(1))
        self.prev_comment_button.clicked.connect(self._on_prev_comment_clicked)
        self.next_comment_button.clicked.connect(self._on_next_comment_clicked)
        # undo/redo — 2026-08-21: no longer draw_overlay.undo/redo directly
        # (that only ever tracked the *current* frame's own strokes); this
        # dialog's own _on_undo/_on_redo below span every frame and both
        # strokes and comments, see _push_undo_snapshot's docstring.
        self.undo_button.clicked.connect(self._on_undo)
        self.redo_button.clicked.connect(self._on_redo)
        self.clear_button.clicked.connect(self.player_widget.draw_overlay.clear_frame)
        self.edit_message_button.clicked.connect(self._on_edit_message_clicked)
        self._prev_comment_shortcut = QShortcut(QKeySequence("Shift+A"), self)
        self._prev_comment_shortcut.activated.connect(self._on_prev_comment_clicked)
        self._next_comment_shortcut = QShortcut(QKeySequence("Shift+D"), self)
        self._next_comment_shortcut.activated.connect(self._on_next_comment_clicked)
        # Ctrl+Z/Ctrl+Shift+Z — moved here from player_widget.py 2026-08-21
        # (see that file's own note) now that undo/redo is a dialog-level,
        # cross-frame concept. _is_typing()-guarded so undoing/redoing while
        # editing the keyframe box or a QInputDialog text field (via
        # pushButton_edit_message) doesn't hijack that field's own native
        # text-undo instead. Default Qt.WindowShortcut context.
        self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self._undo_shortcut.activated.connect(self._on_undo)
        self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        self._redo_shortcut.activated.connect(self._on_redo)
        # Ctrl+S — added 2026-08-21 per the user's own request: a quick save
        # that does NOT close the dialog (unlike clicking Save/pushButton_
        # save_comment, or pressing Enter in a field that happens to submit
        # it) — see _save_comments(close_after=...).
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._on_save_shortcut)

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
        # Read-only 2026-08-21 (was DoubleClicked-to-edit) — editing now
        # goes through pushButton_edit_message/_on_edit_message_clicked
        # instead, per the user's own request.
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.itemSelectionChanged.connect(self._on_table_row_selected)
        # Gray selection highlight, added 2026-08-21 per the user's own
        # request (was Qt's default blue) — targets the item cells
        # specifically rather than QPalette.Highlight, so it doesn't also
        # recolor selection in other widgets sharing this dialog's palette.
        self.table.setStyleSheet(
            "QTableWidget::item:selected { background-color: #5a5a5a; color: white; }"
        )

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

    # -- undo/redo (global, cross-frame — see __init__'s own note) ----------

    def _is_typing(self) -> bool:
        """Guards Ctrl+Z/Ctrl+Shift+Z from hijacking native text-undo in a
        focused text field (the keyframe box, or the QInputDialog
        pushButton_edit_message opens) — same pattern player_widget.py's
        own (now-removed) version used."""
        focus_widget = QApplication.focusWidget()
        return isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit))

    def _push_undo_snapshot(self, frame_index: int) -> None:
        """Records frame_index's state right before a mutation, so
        Undo/Redo can restore it and jump the player back there — the
        single call site is _record_frame_change, which both stroke
        changes (draw/erase/clear) and comment changes (add/edit/delete)
        already funnel through, so this covers exactly those three action
        types without needing to distinguish them explicitly."""
        key = str(frame_index)
        self._undo_stack.append((frame_index, copy.deepcopy(self._frames.get(key, {}))))
        if len(self._undo_stack) > _UNDO_STACK_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()  # a new action invalidates any redo history

    def _apply_frame_snapshot(self, frame_index: int, snapshot: dict) -> None:
        key = str(frame_index)
        if snapshot:
            self._frames[key] = snapshot
        else:
            self._frames.pop(key, None)
        # jump_to_frame always re-emits frameIndexChanged (SequencePlayer.
        # seek() doesn't skip a no-op seek to the same index), which drives
        # _on_frame_index_changed to reload draw_overlay/the table from the
        # snapshot just applied above — so no separate refresh is needed
        # even when frame_index is already the frame on screen.
        self.player_widget.jump_to_frame(frame_index)

    def _on_undo(self) -> None:
        if self._is_typing() or not self._undo_stack:
            return
        frame_index, snapshot = self._undo_stack.pop()
        self._redo_stack.append((frame_index, copy.deepcopy(self._frames.get(str(frame_index), {}))))
        self._apply_frame_snapshot(frame_index, snapshot)

    def _on_redo(self) -> None:
        if self._is_typing() or not self._redo_stack:
            return
        frame_index, snapshot = self._redo_stack.pop()
        self._undo_stack.append((frame_index, copy.deepcopy(self._frames.get(str(frame_index), {}))))
        self._apply_frame_snapshot(frame_index, snapshot)

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
        # The one mutation choke-point for both stroke changes (draw/erase/
        # clear, via _on_strokes_changed) and comment changes (add/edit/
        # delete, via _apply_comment_text/_on_table_context_menu) — pushing
        # the undo snapshot here, before applying the incoming change,
        # covers exactly the three action types the user asked undo/redo to
        # "detect" (draw, erase, comment) and nothing else.
        self._push_undo_snapshot(frame_index)
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
                # Always listed, even for the current frame — see the
                # composer-row note below for how the current frame's own
                # row set and the composer row now stay mutually exclusive
                # instead of duplicating each other.
                for comment in comments:
                    rows.append((frame_index, comment))
            elif frame_index != self._current_frame_index:
                # Has strokes but no text comment yet, and isn't the frame
                # on screen right now — still shows up, per the user's own
                # request, as a blank row you can type into. The current
                # frame's own blank case is covered by the trailing
                # composer row instead, so it isn't duplicated here.
                rows.append((frame_index, None))

        # The composer row (for whichever frame the player is currently on)
        # only gets added when that frame has no comment rows of its own
        # yet — fixed 2026-08-21 after a real duplicate-row report: this
        # used to be appended *unconditionally*, so landing on a frame that
        # already had a comment showed that comment's own row *and* a
        # second, blank "37" row for the exact same frame right below it —
        # confusing, looked like a stray duplicate keyframe rather than "an
        # empty slot to add one more comment". Now the composer row exists
        # only to give an otherwise-row-less current frame something to
        # select and click Edit Message on. bisect_right (when it does
        # apply) places it after any existing rows for that same frame,
        # keeping the whole table in ascending frame order.
        current_key = str(self._current_frame_index)
        if not self._frames.get(current_key, {}).get("comments"):
            insert_at = bisect.bisect_right([r[0] for r in rows], self._current_frame_index)
            rows.insert(insert_at, (self._current_frame_index, None))

        self._suppress_selection_jump = True
        self.table.setRowCount(len(rows))
        for row, (frame_index, comment) in enumerate(rows):
            self._set_row(row, frame_index, comment)
        self._suppress_selection_jump = False

        # Keeps the scrubber's own red comment-frame marks in sync with
        # self._frames — _refresh_table already runs after every mutation
        # and every frame change, so this is the one place that needs it.
        self.player_widget.set_comment_frames(self._keyframe_indices())

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

    def _apply_comment_text(self, frame_index: int, comment_id: str | None, text: str) -> None:
        """Shared by _on_edit_message_clicked — comment_id=None means the
        composer row (add a new comment to frame_index), otherwise edits
        the existing comment with that id in place. Was previously inline
        in _on_table_item_changed (removed 2026-08-21 along with the
        table's own in-cell editing — see the table's setEditTriggers
        note)."""
        comments = list(self._frames.get(str(frame_index), {}).get("comments", []))
        if comment_id is None:
            if not text:
                return
            comments.append(
                {
                    "id": _new_comment_id(),
                    "author": comment_store.current_username(self._api),
                    "text": text,
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            )
        else:
            for comment in comments:
                if comment.get("id") == comment_id:
                    comment["text"] = text
                    break
        self._record_frame_change(frame_index, comments=comments)

    def _on_edit_message_clicked(self) -> None:
        """pushButton_edit_message — added 2026-08-21 alongside making
        tableWidget_comment read-only: the only way to add/edit a
        comment's text now, via a QInputDialog instead of in-cell editing.
        Operates on whichever row is currently selected (the composer row
        for the current frame, or an existing comment row) — but no row
        stays selected after every frame change (_refresh_table rebuilds
        the table's items from scratch, dropping any prior current row), so
        requiring a row selected first meant clicking this button right
        after navigating to a frame silently did nothing. With no row
        selected, this now falls back to the composer behavior for
        whichever frame the player is currently on (comment_id=None — adds
        a new comment there), so the button always works immediately
        without needing a list click first (2026-08-21, per the user's own
        request)."""
        row = self.table.currentRow()
        if row < 0:
            frame_index = self._current_frame_index
            comment_id = None
            current_text = ""
        else:
            item = self.table.item(row, _COL_COMMENT)
            if item is None:
                return
            frame_index = item.data(Qt.UserRole + 1)
            if frame_index is None:
                frame_index = self._current_frame_index
            comment_id = item.data(Qt.UserRole)
            current_text = item.text()
        text, ok = QInputDialog.getText(self, "Edit Comment", "Comment:", text=current_text)
        if not ok:
            return
        self._apply_comment_text(frame_index, comment_id, text.strip())

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
        self._save_comments(close_after=True)

    def _on_save_shortcut(self) -> None:
        """Ctrl+S — added 2026-08-21 per the user's own request: saves
        immediately without closing the dialog, unlike clicking
        pushButton_save_comment itself (close_after=True there)."""
        self._save_comments(close_after=False)

    def _save_comments(self, *, close_after: bool) -> None:
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
            worker.succeeded.connect(lambda: self._on_comment_sync_finished(close_after))
            worker.failed.connect(lambda message: self._on_comment_sync_failed(message, close_after))
            worker.finished.connect(worker.deleteLater)
            self._sync_worker = worker
            worker.start()
            return
        if close_after:
            self.accept()

    def _on_comment_sync_finished(self, close_after: bool) -> None:
        self._sync_worker = None
        self.save_button.setEnabled(True)
        _logger.info("comments.json cloud sync succeeded")
        if close_after:
            self.accept()

    def _on_comment_sync_failed(self, message: str, close_after: bool) -> None:
        self._sync_worker = None
        self.save_button.setEnabled(True)
        _logger.warning("comments.json cloud sync failed: %s", message)
        QMessageBox.warning(self, "Cloud Sync Failed", "Comments saved locally, but the cloud sync failed: " + message)
        if close_after:
            self.accept()


def _new_comment_id() -> str:
    return uuid.uuid4().hex[:8]

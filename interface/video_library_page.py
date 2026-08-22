from __future__ import annotations

import datetime
import fnmatch
import logging
import re
import shutil
import subprocess
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFile, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ukoreshot_plugin.core import comment_store, video_naming, video_path_store, video_sequence
from ukoreshot_plugin.core.video_compress import VideoCompressionError, burn_frame_numbers, compress_to_fit, get_ffmpeg_path
from ukoreshot_plugin.core.share_sync import (
    PullByCodeWorker,
    ShareUploadWorker,
    SyncSharedCommentsWorker,
    generate_unique_share_code,
    pull_comments,
    push_pointer,
)
from ukoreshot_plugin.interface.comment_editor import CommentEditor
from ukoreshot_plugin.interface.draw_overlay import Stroke, paint_stroke_points
from ukoreshot_plugin.interface.player_widget import PlayerWidget, paint_frame_number
from ukoreshot_plugin.interface.thumbnail_loader import ThumbnailLoader

_logger = logging.getLogger("UkoreShot.Library")

_UI_FILE = Path(__file__).resolve().parent / "UkoreShotPage.ui"
_VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi"}
_ICON_SIZE = QSize(160, 100)
# Hard cap for Get Video / Get Video - Commented, per the user's own
# request — independent of the (now-removed) Discord Max Upload Size.
_MAX_EXPORT_BYTES = 20 * 1024 * 1024

# This plugin's own images/ folder — see player_widget.py's own _ICONS_DIR
# note for why (not the shared data/icons/ every other plugin uses).
_ICONS_DIR = Path(__file__).resolve().parent.parent / "images"
_SHARE_ICON_PATH = _ICONS_DIR / "share.png"
_SHARE_ICON_SIZE = QSize(20, 20)

_COL_THUMBNAIL, _COL_NAME, _COL_SHARED, _COL_SHARE_CODE, _COL_DATE, _COL_TIME_AGO = range(6)

_SORT_NAME_ASC = "name_asc"
_SORT_NEWEST = "newest"
_DEFAULT_SORT = _SORT_NEWEST

# Matches comment_store.generate_share_code's output shape — used by the
# search bar's Enter-key handler to tell "someone pasted a share code"
# apart from a plain wildcard search string, without needing a separate
# dedicated field. The leading "UKSHOT_" is optional so a code generated
# before that prefix was added 2026-08-21 (see generate_share_code's own
# docstring) still matches — old codes were never migrated/rewritten to
# add it, they just keep working exactly as they were.
_SHARE_CODE_PATTERN = re.compile(r"^(?:UKSHOT_)?[A-Za-z0-9]+_v\d{3}_[0-9A-Fa-f]{4}$")


@dataclass
class _LibraryEntry:
    """One row in tableWidget_playblast_library. video_path is None for a
    video that only exists here because it was pulled in by share code
    (core/share_sync.py's PullByCodeWorker) — no local video file for it at
    all, only its already-extracted sequence_dir. Every other entry has a
    real video_path and a sequence_dir that may or may not have been
    extracted yet (video_sequence.has_sequence)."""

    key: str
    video_path: Path | None
    sequence_dir: Path
    stem: str
    parsed: dict | None
    mtime: float
    share_state: dict


def _format_time_ago(mtime: float) -> str:
    delta_seconds = max(0.0, datetime.datetime.now().timestamp() - mtime)
    if delta_seconds < 60:
        return "just now"
    if delta_seconds < 3600:
        return "{}m ago".format(int(delta_seconds // 60))
    if delta_seconds < 86400:
        return "{}h ago".format(int(delta_seconds // 3600))
    if delta_seconds < 2592000:
        return "{}d ago".format(int(delta_seconds // 86400))
    return "{}mo ago".format(int(delta_seconds // 2592000))


def _reveal_and_select_in_explorer(path: Path) -> None:
    """Opens Windows Explorer at path's containing folder with path itself
    already selected/highlighted — /select, does both in one call, so
    there's no separate "open the folder" step needed first. Windows-only,
    matching this plugin's other Windows-only conventions (e.g. the ffmpeg
    win64 auto-download). explorer.exe's own exit code is unreliable even
    on success, so this doesn't check it."""
    subprocess.run(["explorer", "/select,", str(path)])


def _wildcard_match(pattern: str, text: str) -> bool:
    """Plain typing (no "*"/"?") behaves like the old substring search —
    wrapped as "*text*" first — while a real wildcard pattern is used as
    typed. Matched against the lowercased text the same way the old
    substring check compared against a lowercased relative path."""
    if not pattern:
        return True
    if "*" not in pattern and "?" not in pattern:
        pattern = "*{}*".format(pattern)
    return fnmatch.fnmatch(text.lower(), pattern.lower())


class UkoreShotPage(QWidget):
    """The UkoreShot sidebar tab's page — rebuilt 2026-08-20 against the
    user's own UkoreShotPage.ui (QUiLoader, same pattern
    plugins/core/explorer/browser_widget.py and
    plugins/core/ExternalPluginManager/external_plugins_page.py already use
    in the host app — the .ui only supplies layout/widget identity, not
    behavior, same convention those two follow). groupBox_playblast_viewer
    gets a PlayerWidget inserted at runtime; tableWidget_playblast_library
    (Thumbnail/Name/Shared/Share Code/Date/Time Ago) replaces the old
    FlowLayout card grid + FilterSidebar entirely — confirmed with the user
    this round that per-category filtering is retired in favor of just the
    wildcard search bar + sort buttons this .ui actually has. The search bar
    also matches against a video's share code (not just its stem), so
    pasting/typing an already-local code filters straight to that row.

    Video->image-sequence splitting is lazy (core/video_sequence.py),
    triggered only from pushButton_comment/pushButton_copy_clipboard's own
    handlers — never from a reload/refresh scan, per the user's explicit
    "don't convert every video just from browsing" instruction. Edit
    Comment (pushButton_comment) opens the in-house CommentEditor directly
    (see comment_editor.py) — PlayerWidget no longer has its own duplicate
    Edit Comment button (removed 2026-08-21, this page's own button already
    covers it).

    Discord support removed entirely 2026-08-21 (standalone tool now) —
    pushButton_get_video paired with checkBox_display_comment_download
    replace the old Discord-oriented "get format video"/"auto send to
    Discord" buttons: generates a local .mp4 (hard-capped at 20MB) into a
    cache-only export folder that's never touched by cloud sync, and
    reveal+select it in Explorer — see _on_get_video_clicked below.
    Mark as Share and Copy Share Code were merged into the single
    pushButton_copy_clipboard (2026-08-22, per the user's own request):
    its label/action switch on whether the selected entry is already
    shared ("Make Share" when not, "Copy Code" once it is) — see
    _on_copy_clipboard_clicked below.

    **Shared status is an icon, not text, as of 2026-08-21** — share.png
    via a small QLabel cell widget (_make_shared_cell_widget), not
    item.setIcon(), since the table's own iconSize (_ICON_SIZE, the large
    thumbnail size) applies to every item icon in the view uniformly and
    would blow a status icon up to thumbnail size too.

    **widget_status_loading (label_loading + progressBar), same day**: a
    generic busy indicator (_show_status/_set_status_message/_hide_status)
    shown around every long-running operation this page runs — sequence
    extraction, Get Video/Commented, Mark as Share's upload, pull-by-code,
    ffmpeg resolution/download. The bar is always indeterminate (no real
    percentage reported by any of these today). The synchronous ones
    (extraction, compression, ffmpeg download) still block this thread
    while they run, same as before — QApplication.processEvents() in
    _show_status/_set_status_message just forces the label/bar to actually
    repaint immediately before that blocking call starts, rather than only
    becoming visible once the whole operation (and the paint events queued
    behind it) has already finished."""

    def __init__(self, parent=None, *, api):
        super().__init__(parent)
        _logger.info("UkoreShotPage.__init__ starting")
        self._api = api
        self._project_id: str | None = None
        self._repo_id: str | None = None
        self._video_root: Path | None = None
        self._entries_by_key: dict[str, _LibraryEntry] = {}
        self._selected_key: str | None = None
        self._sort_mode = _DEFAULT_SORT
        self._current_frame_index = 0
        # pushButton_show_comment_toggle state — _current_entry_frames is
        # the selected entry's whole comments.json "frames" dict, cached
        # once per selection (not re-read from disk on every frame tick
        # during playback — see _load_selected_entry/_refresh_frame_strokes).
        self._show_comments = False
        self._current_entry_frames: dict = {}
        self._share_worker: ShareUploadWorker | None = None
        self._pull_worker: PullByCodeWorker | None = None
        self._comments_sync_worker: SyncSharedCommentsWorker | None = None
        self._thumbnail_loader = ThumbnailLoader(self)
        self._thumbnail_loader.thumbnailReady.connect(self._on_thumbnail_ready)

        _logger.debug("loading %s", _UI_FILE)
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
        self.table: QTableWidget = find(QTableWidget, "tableWidget_playblast_library")
        self.search_edit: QLineEdit = find(QLineEdit, "lineEdit_search_bar")
        self.reload_button: QPushButton = find(QPushButton, "pushButton_reload")
        self.sort_ascending_button: QPushButton = find(QPushButton, "pushButton_sort_ascending")
        self.sort_newest_button: QPushButton = find(QPushButton, "pushButton_sort_newest")
        self.comment_button: QPushButton = find(QPushButton, "pushButton_comment")
        self.delete_button: QPushButton = find(QPushButton, "pushButton_delete_playblast")
        # Doubles as Mark as Share (not yet shared) / Copy Share Code
        # (already shared) — see _update_button_states/_on_copy_clipboard_clicked.
        self.copy_clipboard_button: QPushButton = find(QPushButton, "pushButton_copy_clipboard")
        self.get_video_button: QPushButton = find(QPushButton, "pushButton_get_video")
        # Paired with pushButton_get_video — checked routes the export
        # through the "commented" (drawings + frame number burned in)
        # render path instead of the plain one.
        self.display_comment_download_checkbox: QCheckBox = find(QCheckBox, "checkBox_display_comment_download")
        self.prev_frame_button: QPushButton = find(QPushButton, "pushButton_previous_frame")
        self.play_button: QPushButton = find(QPushButton, "pushButton_play")
        self.next_frame_button: QPushButton = find(QPushButton, "pushButton_next_frame")
        self.prev_comment_button: QPushButton = find(QPushButton, "pushButton_previous_comment")
        self.next_comment_button: QPushButton = find(QPushButton, "pushButton_next_comment")
        self.speed_combo: QComboBox = find(QComboBox, "comboBox_speed")
        self.keyframe_edit: QLineEdit = find(QLineEdit, "lineEdit_keyframe")
        # Toggles player_widget.py's _CommentOverlay — the current frame's
        # saved strokes shown over the video, without opening the full
        # CommentEditor (2026-08-21, per the user's own request; see
        # _on_show_comment_toggle/_refresh_frame_strokes below).
        self.show_comment_toggle_button: QPushButton = find(QPushButton, "pushButton_show_comment_toggle")
        # "Current Keyframe Comment" box — always shows whichever comment
        # text is saved on the frame currently on screen, independent of
        # pushButton_show_comment_toggle (that one only governs the drawing
        # overlay). See _refresh_comment_text below.
        self.comment_text_edit: QPlainTextEdit = find(QPlainTextEdit, "plainTextEdit_comment")
        # Generic busy indicator, added 2026-08-21 — shown/hidden around
        # every long-running operation this page runs (sequence extraction,
        # Get Video/Commented, Mark as Share upload, pull-by-code, ffmpeg
        # resolution/download); see _show_status/_set_status_message/
        # _hide_status below.
        self.status_widget: QWidget = find(QWidget, "widget_status_loading")
        self.status_label: QLabel = find(QLabel, "label_loading")
        self.status_progress: QProgressBar = find(QProgressBar, "progressBar")

        for _name, _widget in [
            ("groupBox_playblast_viewer", self.viewer_group),
            ("tableWidget_playblast_library", self.table),
            ("lineEdit_search_bar", self.search_edit),
            ("pushButton_reload", self.reload_button),
            ("pushButton_sort_ascending", self.sort_ascending_button),
            ("pushButton_sort_newest", self.sort_newest_button),
            ("pushButton_comment", self.comment_button),
            ("pushButton_delete_playblast", self.delete_button),
            ("pushButton_copy_clipboard", self.copy_clipboard_button),
            ("pushButton_get_video", self.get_video_button),
            ("checkBox_display_comment_download", self.display_comment_download_checkbox),
            ("pushButton_previous_frame", self.prev_frame_button),
            ("pushButton_play", self.play_button),
            ("pushButton_next_frame", self.next_frame_button),
            ("pushButton_previous_comment", self.prev_comment_button),
            ("pushButton_next_comment", self.next_comment_button),
            ("comboBox_speed", self.speed_combo),
            ("lineEdit_keyframe", self.keyframe_edit),
            ("pushButton_show_comment_toggle", self.show_comment_toggle_button),
            ("plainTextEdit_comment", self.comment_text_edit),
            ("widget_status_loading", self.status_widget),
            ("label_loading", self.status_label),
            ("progressBar", self.status_progress),
        ]:
            if _widget is None:
                _logger.error("UkoreShotPage.ui has no widget named %r — findChild returned None", _name)
        if self.status_widget is not None:
            self.status_widget.setVisible(False)

        # Player lives inside the .ui's own empty placeholder groupbox —
        # same "insert a real widget into a Designer-authored empty
        # QGroupBox at runtime" convention groupBox_playblast_viewer in
        # comment_editor.py's CommentEditor.ui also uses.
        # own_transport_controls=False: this .ui now supplies its own
        # prev/play/next-frame, lineEdit_keyframe, and comboBox_speed (see
        # below) instead of PlayerWidget building duplicate ones in code.
        self.player_widget = PlayerWidget(own_transport_controls=False)
        self.player_widget.playingChanged.connect(self._on_playing_changed)
        self.player_widget.frameIndexChanged.connect(self._on_frame_index_changed)
        viewer_layout = QVBoxLayout(self.viewer_group)
        viewer_layout.setContentsMargins(4, 16, 4, 4)
        viewer_layout.addWidget(self.player_widget)

        PlayerWidget._set_button_icon(self.prev_frame_button, _ICONS_DIR / "icons8-chevron-left-26.png", "<")
        PlayerWidget._set_button_icon(self.play_button, _ICONS_DIR / "icons8-play-50.png", "Play")
        PlayerWidget._set_button_icon(self.next_frame_button, _ICONS_DIR / "icons8-right-26.png", ">")
        PlayerWidget._set_button_icon(self.prev_comment_button, _ICONS_DIR / "prev_comment.png", "< Comment")
        PlayerWidget._set_button_icon(self.next_comment_button, _ICONS_DIR / "next_comment.png", "Comment >")
        self.prev_frame_button.clicked.connect(lambda: self.player_widget.step_frame(-1))
        self.play_button.clicked.connect(self.player_widget.toggle_play)
        self.next_frame_button.clicked.connect(lambda: self.player_widget.step_frame(1))
        self.prev_comment_button.clicked.connect(self._on_prev_comment_clicked)
        self.next_comment_button.clicked.connect(self._on_next_comment_clicked)
        # Toggles _CommentOverlay (player_widget.py) — off by default, shows
        # the current frame's saved strokes over the video without needing
        # to open the full CommentEditor. setCheckable so Qt's own
        # pressed/checked style also indicates state; the icon itself swaps
        # too (2026-08-21, per the user's own request) — comment_mode.png
        # while showing comments, icons8-video-50.png (a plain video icon)
        # while off — see _update_show_comment_icon.
        self.show_comment_toggle_button.setCheckable(True)
        self.show_comment_toggle_button.toggled.connect(self._on_show_comment_toggle)
        self._update_show_comment_icon()

        # 0.25x-1.00x discrete steps — comboBox_speed replaces the old
        # continuous speed_slider for this page only (CommentEditor keeps
        # its own slider, no .ui equivalent there).
        self.speed_combo.addItems(["0.25x", "0.5x", "0.75x", "1.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self._on_speed_combo_changed)

        self.keyframe_edit.setPlaceholderText("Frame")
        self.keyframe_edit.returnPressed.connect(self._on_keyframe_edit_entered)

        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["", "Name", "Shared", "Share Code", "Date", "Time Ago"])
        self.table.setIconSize(_ICON_SIZE)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.verticalHeader().setDefaultSectionSize(_ICON_SIZE.height() + 12)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_THUMBNAIL, QHeaderView.Fixed)
        self.table.setColumnWidth(_COL_THUMBNAIL, _ICON_SIZE.width() + 8)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_SHARED, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_SHARE_CODE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_DATE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_TIME_AGO, QHeaderView.ResizeToContents)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.returnPressed.connect(self._on_search_enter)
        self.reload_button.clicked.connect(self._reload_videos_and_sync)
        self.sort_ascending_button.clicked.connect(lambda: self._set_sort_mode(_SORT_NAME_ASC))
        self.sort_newest_button.clicked.connect(lambda: self._set_sort_mode(_SORT_NEWEST))
        self.comment_button.clicked.connect(self._on_edit_comment_clicked)
        self.copy_clipboard_button.clicked.connect(self._on_copy_clipboard_clicked)
        self.get_video_button.clicked.connect(self._on_get_video_clicked)
        self.delete_button.clicked.connect(self._on_delete_playblast_clicked)
        if self.display_comment_download_checkbox is not None:
            # Enabled state of pushButton_get_video depends on it (see
            # _update_button_states) — a video-less, sequence-only entry can
            # only be exported with the checkbox checked.
            self.display_comment_download_checkbox.toggled.connect(lambda _checked: self._update_button_states())
        self.copy_clipboard_button.setEnabled(False)

        # Shift+A/Shift+D — jump to the previous/next commented keyframe of
        # the selected entry, same shortcut comment_editor.py's own
        # CommentEditor already wires to its own Previous/Next Comment
        # buttons (see that file's __init__) — this page had the buttons but
        # no keyboard shortcut for them until now.
        self._prev_comment_shortcut = QShortcut(QKeySequence("Shift+A"), self)
        self._prev_comment_shortcut.activated.connect(self._on_prev_comment_clicked)
        self._next_comment_shortcut = QShortcut(QKeySequence("Shift+D"), self)
        self._next_comment_shortcut.activated.connect(self._on_next_comment_clicked)

        self._update_button_states()
        _logger.info("UkoreShotPage.__init__ finished")

    # -- standard page protocol -------------------------------------------

    def set_repo(self, project, repo, workspace_root: str) -> None:
        self._project_id = project.id if project is not None else None
        self._repo_id = repo.id if repo is not None else None
        _logger.info("set_repo(project_id=%s, repo_id=%s)", self._project_id, self._repo_id)
        self._reload_videos_and_sync()

    # -- transport row (own_transport_controls=False) -----------------------

    def _on_playing_changed(self, playing: bool) -> None:
        PlayerWidget._set_button_icon(
            self.play_button,
            _ICONS_DIR / "icons8-pause-50.png" if playing else _ICONS_DIR / "icons8-play-50.png",
            "Pause" if playing else "Play",
        )

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

    def _selected_entry_keyframe_indices(self) -> list[int]:
        """Frames with a saved comment or drawing for the currently
        selected entry — read straight from comments.json, same "keyframe"
        definition comment_editor.py's own _keyframe_indices uses. Safe to
        call even when no sequence has been extracted yet for this entry
        (comment_store.load returns empty frames for a nonexistent file/
        directory) — Previous/Next Comment just has nothing to jump to."""
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None:
            return []
        frames = comment_store.load(entry.sequence_dir)["frames"]
        indices = [int(key) for key, data in frames.items() if data.get("comments") or data.get("strokes")]
        return sorted(indices)

    def _on_prev_comment_clicked(self) -> None:
        indices = self._selected_entry_keyframe_indices()
        if not indices:
            return
        earlier = [i for i in indices if i < self._current_frame_index]
        self.player_widget.jump_to_frame(earlier[-1] if earlier else indices[-1])

    def _on_next_comment_clicked(self) -> None:
        indices = self._selected_entry_keyframe_indices()
        if not indices:
            return
        later = [i for i in indices if i > self._current_frame_index]
        self.player_widget.jump_to_frame(later[0] if later else indices[0])

    def _on_frame_index_changed(self, frame_index: int) -> None:
        self._current_frame_index = frame_index
        # Kept in sync with the player, not just a write-only entry field —
        # same as comment_editor.py's own lineEdit_keyframe.
        self.keyframe_edit.setText(str(frame_index))
        self._refresh_frame_strokes()
        self._refresh_comment_text()

    # -- show comment overlay (pushButton_show_comment_toggle) --------------

    def _on_show_comment_toggle(self, checked: bool) -> None:
        self._show_comments = checked
        self.player_widget.set_comments_visible(checked)
        self._refresh_frame_strokes()
        self._update_show_comment_icon()

    def _update_show_comment_icon(self) -> None:
        icon_path = _ICONS_DIR / ("comment_mode.png" if self._show_comments else "icons8-video-50.png")
        PlayerWidget._set_button_icon(self.show_comment_toggle_button, icon_path, "Comments")

    def _refresh_frame_strokes(self) -> None:
        """Feeds player_widget.py's _CommentOverlay whichever frame is
        currently on screen, reading from self._current_entry_frames — a
        cached copy of the selected entry's comments.json "frames" dict
        (see _load_selected_entry), not a fresh disk read on every call, so
        this stays cheap even called on every single frame tick during
        playback."""
        if not self._show_comments:
            self.player_widget.set_frame_strokes([])
            return
        data = self._current_entry_frames.get(str(self._current_frame_index), {})
        self.player_widget.set_frame_strokes([Stroke.from_dict(s) for s in data.get("strokes", [])])

    def _refresh_comment_text(self) -> None:
        """Fills plainTextEdit_comment with whichever text comment(s) are
        saved on the frame currently on screen — reads from the same cached
        self._current_entry_frames _refresh_frame_strokes uses, so this
        stays cheap called on every frame tick during playback too. Always
        kept up to date regardless of pushButton_show_comment_toggle (that
        one only governs the drawing overlay, not this box). A frame with
        multiple comments (Facebook-style, see comment_store.py) shows each
        on its own line, blank when there's none."""
        if self.comment_text_edit is None:
            return
        data = self._current_entry_frames.get(str(self._current_frame_index), {})
        comments = data.get("comments", [])
        self.comment_text_edit.setPlainText("\n".join(c.get("text", "") for c in comments if c.get("text")))

    # -- busy status (widget_status_loading) ---------------------------------

    def _show_status(self, message: str) -> None:
        """Shown by whichever click handler starts a long-running operation
        (sequence extraction, Get Video/Commented, Mark as Share upload,
        pull-by-code, ffmpeg resolution/download) — see _set_status_message
        for the nested helpers (_resolve_ffmpeg, _ensure_sequence_for,
        _render_commented_video) that update the message as the operation
        progresses without re-triggering visibility. None of these
        operations report a real percentage today, so the bar is always
        indeterminate ("busy") rather than a true progress fraction."""
        if self.status_widget is None:
            return
        self.status_label.setText(message)
        self.status_progress.setRange(0, 0)
        self.status_widget.setVisible(True)
        # A synchronous ffmpeg/compress call blocks this thread right after
        # this returns — force the label/bar to actually repaint first, or
        # the user would never see them until the operation is already done.
        QApplication.processEvents()

    def _set_status_message(self, message: str) -> None:
        if self.status_widget is None or not self.status_widget.isVisible():
            return
        self.status_label.setText(message)
        QApplication.processEvents()

    def _hide_status(self) -> None:
        if self.status_widget is None:
            return
        self.status_widget.setVisible(False)

    # -- video list ---------------------------------------------------------

    def _resolve_ffmpeg(self) -> str | None:
        self._set_status_message("Resolving ffmpeg...")
        try:
            path = video_sequence.resolve_ffmpeg_path(get_ffmpeg_path(self._api), self._api.cache_dir)
            _logger.debug("resolved ffmpeg path: %s", path)
            return path
        except VideoCompressionError as exc:
            _logger.warning("ffmpeg not resolvable: %s", exc)
            QMessageBox.warning(self, "ffmpeg Required", str(exc))
            return None

    def _reload_videos(self) -> None:
        self._selected_key = None
        self._video_root = None
        self._entries_by_key = {}
        if self._project_id and self._repo_id:
            self._video_root = video_path_store.resolve_video_root(self._api, self._project_id, self._repo_id)
        _logger.debug("_reload_videos: video_root=%s", self._video_root)
        self._update_empty_state()
        if self._video_root is None or not self._video_root.is_dir():
            self._apply_filter()
            return

        # Recursive: a video flat-named under UkorePlayblast's naming
        # convention lives directly in video_root, but an older playblast
        # may still sit nested under its own <sequence>/<shot_code>/vNNN/
        # subfolder (left alone there per the user's own decision — see
        # maya-scripts/README.md) — both need to show up here.
        video_paths = [
            p for p in self._video_root.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS
        ]
        stems_with_video = {p.stem for p in video_paths}
        for video_path in video_paths:
            sequence_dir = video_sequence.sequence_dir_for(video_path)
            entry = _LibraryEntry(
                key=str(video_path),
                video_path=video_path,
                sequence_dir=sequence_dir,
                stem=video_path.stem,
                parsed=video_naming.parse_video_filename(video_path),
                mtime=video_path.stat().st_mtime,
                share_state=comment_store.get_share_state(sequence_dir),
            )
            self._entries_by_key[entry.key] = entry

        # Sequence-only entries: a <stem>/comments.json with no matching
        # local video file — arrived purely via a pasted share code
        # (core/share_sync.py's PullByCodeWorker). Never triggers
        # ensure_sequence — just reflects what's already on disk.
        for metadata_path in self._video_root.rglob("comments.json"):
            sequence_dir = metadata_path.parent
            if sequence_dir.name in stems_with_video:
                continue
            entry = _LibraryEntry(
                key=str(sequence_dir),
                video_path=None,
                sequence_dir=sequence_dir,
                stem=sequence_dir.name,
                parsed=video_naming.parse_video_filename(Path(sequence_dir.name)),
                mtime=metadata_path.stat().st_mtime,
                share_state=comment_store.get_share_state(sequence_dir),
            )
            self._entries_by_key[entry.key] = entry

        _logger.info(
            "_reload_videos: %d video file(s), %d sequence-only entr(y/ies)",
            len(video_paths), len(self._entries_by_key) - len(video_paths),
        )
        self._apply_filter()

    def _reload_videos_and_sync(self) -> None:
        """pushButton_reload / set_repo (tab open, repo switch) — reloads
        local state first (fast, no network, same as _reload_videos alone
        everywhere else), then kicks off a background pull of every
        already-shared local entry's comments.json so Reload/opening the
        tab also catches up with whatever anyone else most recently
        pushed (Mark as Share or an incremental comment save elsewhere) —
        added 2026-08-21, per the user's own request. Not used by every
        _reload_videos() call site — only these two explicit user actions,
        not e.g. after a delete or a share-upload success, which don't
        need a full re-sync of every *other* entry too."""
        self._reload_videos()
        if self._api.cloud_sync is None or self._project_id is None or self._repo_id is None:
            return
        shared_dirs = [e.sequence_dir for e in self._entries_by_key.values() if e.share_state.get("is_shared")]
        if not shared_dirs:
            return
        worker = SyncSharedCommentsWorker(
            self._api.cloud_sync,
            project_id=self._project_id,
            repo_id=self._repo_id,
            sequence_dirs=shared_dirs,
            parent=self,
        )
        worker.succeeded.connect(self._on_shared_comments_synced)
        worker.finished.connect(worker.deleteLater)
        self._comments_sync_worker = worker
        worker.start()

    def _on_shared_comments_synced(self) -> None:
        self._comments_sync_worker = None
        # Re-reads whatever SyncSharedCommentsWorker just pulled — a second
        # _reload_videos() (not _reload_videos_and_sync(), which would just
        # kick off another sync pass and loop) so the table/scrubber marks
        # reflect any comments.json that actually changed. Explicitly
        # restores whatever was selected before this background sync ran
        # (_reload_videos() always resets to its own "most recently
        # modified" default otherwise) so a sync completing in the
        # background doesn't yank the viewer away to a different entry.
        previously_selected_key = self._selected_key
        self._reload_videos()
        if previously_selected_key is not None:
            self._select_row_by_key(previously_selected_key)

    def _set_sort_mode(self, mode: str) -> None:
        self._sort_mode = mode
        self._apply_filter()

    def _sort_entries(self, entries: list[_LibraryEntry]) -> list[_LibraryEntry]:
        if self._sort_mode == _SORT_NAME_ASC:
            return sorted(entries, key=lambda e: e.stem.lower())
        return sorted(entries, key=lambda e: e.mtime, reverse=True)  # _SORT_NEWEST, the default

    def _make_shared_cell_widget(self, is_shared: bool) -> QLabel:
        """Shared column, added 2026-08-21 — an icon (share.png) instead of
        the word "Shared" per the user's own request. Empty label (no
        pixmap) for a not-yet-shared entry, matching the old "—" placeholder
        without needing a second icon asset."""
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        if is_shared and _SHARE_ICON_PATH.is_file():
            pixmap = QPixmap(str(_SHARE_ICON_PATH)).scaled(
                _SHARE_ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            label.setPixmap(pixmap)
        return label

    def _apply_filter(self) -> None:
        search = self.search_edit.text().strip()
        # Matches against a video's own share code too (not just its stem)
        # so pasting/typing an already-local code filters straight to that
        # row — a separate concern from _on_search_enter's own "pull a
        # not-yet-local code from the cloud" handling below.
        entries = [
            e for e in self._entries_by_key.values()
            if _wildcard_match(search, e.stem) or _wildcard_match(search, e.share_state.get("code") or "")
        ]
        entries = self._sort_entries(entries)

        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.stem)
            name_item.setData(Qt.UserRole, entry.key)
            self.table.setItem(row, _COL_THUMBNAIL, QTableWidgetItem())
            self.table.setItem(row, _COL_NAME, name_item)
            self.table.setItem(row, _COL_SHARED, QTableWidgetItem())
            # A cell widget, not item.setIcon() — the table's own iconSize
            # (_ICON_SIZE, the large thumbnail size) applies to every
            # QTableWidgetItem icon in the view uniformly, which would blow
            # this up to thumbnail size too; a small QLabel here renders at
            # whatever size it's actually given instead.
            self.table.setCellWidget(row, _COL_SHARED, self._make_shared_cell_widget(entry.share_state["is_shared"]))
            self.table.setItem(row, _COL_SHARE_CODE, QTableWidgetItem(entry.share_state.get("code") or ""))
            date_text = datetime.datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M")
            self.table.setItem(row, _COL_DATE, QTableWidgetItem(date_text))
            self.table.setItem(row, _COL_TIME_AGO, QTableWidgetItem(_format_time_ago(entry.mtime)))
            self._request_thumbnail(entry)

        self._restore_or_default_selection(entries)

    def _request_thumbnail(self, entry: _LibraryEntry) -> None:
        if entry.video_path is not None:
            self._thumbnail_loader.request(entry.video_path)
            return
        # Sequence-only entry — no video file to decode a frame from, load
        # its own first frame directly instead.
        frames = sorted(p for p in entry.sequence_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"})
        if not frames:
            return
        image = QImage(str(frames[0]))
        if not image.isNull():
            self._set_row_thumbnail(entry.key, QPixmap.fromImage(image))

    def _on_thumbnail_ready(self, video_path_str: str, pixmap: QPixmap) -> None:
        self._set_row_thumbnail(video_path_str, pixmap)

    def _set_row_thumbnail(self, key: str, pixmap: QPixmap) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_NAME)
            if item is not None and item.data(Qt.UserRole) == key:
                thumb_item = self.table.item(row, _COL_THUMBNAIL)
                if thumb_item is not None:
                    thumb_item.setIcon(QIcon(pixmap.scaled(_ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
                return

    def _restore_or_default_selection(self, entries: list[_LibraryEntry]) -> None:
        """Same "keep the prior selection if it's still visible, else
        default to the most recent" behavior the old card grid had — see
        the pre-2026-08-20 version of this method for the original
        reasoning, unchanged here."""
        if not entries:
            self._selected_key = None
            self.player_widget.clear_video()
            self._update_button_states()
            return
        target_key = self._selected_key
        if target_key is None or target_key not in {e.key for e in entries}:
            target_key = max(entries, key=lambda e: e.mtime).key
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_NAME)
            if item is not None and item.data(Qt.UserRole) == target_key:
                self.table.blockSignals(True)
                self.table.selectRow(row)
                self.table.blockSignals(False)
                break
        self._selected_key = target_key
        self._load_selected_entry()
        self._update_button_states()
        _logger.debug("_restore_or_default_selection: selected %s", target_key)

    def _on_row_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self._selected_key = None
            self.player_widget.clear_video()
        else:
            item = self.table.item(row, _COL_NAME)
            self._selected_key = item.data(Qt.UserRole) if item is not None else None
            self._load_selected_entry()
        self._update_button_states()

    def _load_selected_entry(self) -> None:
        # Reset before loading — otherwise Previous/Next Comment could
        # briefly use the *previous* video's frame index if clicked before
        # the new one's first frameIndexChanged arrives.
        self._current_frame_index = 0
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None:
            self.player_widget.clear_video()
            self.player_widget.set_comment_frames([])
            self._current_entry_frames = {}
            self._refresh_frame_strokes()
            self._refresh_comment_text()
            return
        if entry.video_path is not None:
            self.player_widget.load_video(entry.video_path)
        else:
            self.player_widget.load_sequence(entry.sequence_dir)
        # Scrubber's own red comment-frame marks, added 2026-08-21 —
        # reads straight off comments.json, same as Previous/Next Comment,
        # so this never forces a sequence extraction just to show them.
        self.player_widget.set_comment_frames(self._selected_entry_keyframe_indices())
        # Cached once per selection (not re-read on every frame tick) for
        # _refresh_frame_strokes — see that method and pushButton_show_
        # comment_toggle's _on_show_comment_toggle.
        self._current_entry_frames = comment_store.load(entry.sequence_dir)["frames"]
        self._refresh_frame_strokes()
        self._refresh_comment_text()

    def _update_empty_state(self) -> None:
        # No dedicated empty-state widget in the new .ui (unlike the old
        # empty_label/content_widget split) — an empty table + a cleared
        # player already communicate "nothing here yet" on its own.
        pass

    def _selected_entries(self) -> list[_LibraryEntry]:
        """Every row in the current (possibly multi-row) table selection —
        used by the Delete button, unlike self._selected_key/_selected_entry
        which only ever track the single "current" row the player previews
        (table.currentRow() stays well-defined under ExtendedSelection too,
        so that preview logic doesn't need to change for multi-select)."""
        entries = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), _COL_NAME)
            key = item.data(Qt.UserRole) if item is not None else None
            entry = self._entries_by_key.get(key) if key else None
            if entry is not None:
                entries.append(entry)
        return entries

    def _update_button_states(self) -> None:
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        has_selection = entry is not None
        is_shared = has_selection and entry.share_state["is_shared"]
        self.comment_button.setEnabled(has_selection)
        # pushButton_copy_clipboard does double duty (2026-08-22): Make
        # Share for a not-yet-shared entry, Copy Code once it is — see
        # _on_copy_clipboard_clicked. Enabled whenever something's
        # selected; which action it takes is decided at click time.
        self.copy_clipboard_button.setEnabled(has_selection)
        self.copy_clipboard_button.setText("Copy Code" if is_shared else "Make Share")
        # checkBox_display_comment_download checked routes pushButton_get_video
        # through the "commented" render path, which works off the sequence
        # (extractable for any entry) rather than needing a real local video
        # file the way the plain export does.
        wants_comments = has_selection and self.display_comment_download_checkbox is not None and self.display_comment_download_checkbox.isChecked()
        if wants_comments:
            self.get_video_button.setEnabled(has_selection)
        else:
            self.get_video_button.setEnabled(has_selection and entry.video_path is not None)
        self.delete_button.setEnabled(bool(self._selected_entries()))

    # -- share-code search-bar round-trip -----------------------------------

    def _on_search_enter(self) -> None:
        text = self.search_edit.text().strip()
        if not _SHARE_CODE_PATTERN.match(text):
            return
        if self._video_root is None:
            return
        if any(e.share_state.get("code") == text for e in self._entries_by_key.values()):
            _logger.debug("share code %s already local, skipping pull", text)
            return  # already local, nothing to pull
        if self._api.cloud_sync is None:
            _logger.warning("share code %s entered but api.cloud_sync is None", text)
            QMessageBox.warning(self, "Cloud Sync Unavailable", "Cloud sync isn't configured on this machine.")
            return
        _logger.info("pulling shared video for code %s", text)
        self.search_edit.setEnabled(False)
        self._show_status("Pulling shared video...")
        worker = PullByCodeWorker(self._api.cloud_sync, text, video_root=self._video_root, parent=self)
        worker.succeeded.connect(self._on_pull_by_code_succeeded)
        worker.not_found.connect(self._on_pull_by_code_not_found)
        worker.failed.connect(self._on_pull_by_code_failed)
        worker.finished.connect(worker.deleteLater)
        self._pull_worker = worker
        worker.start()

    def _on_pull_by_code_succeeded(self, stem: str) -> None:
        self._pull_worker = None
        self.search_edit.setEnabled(True)
        self._hide_status()
        _logger.info("pull by code succeeded: %s", stem)
        # No confirmation dialog (per the user's own request) — clear the
        # search box first (it still holds the share code, which wouldn't
        # match the pulled entry's actual filename via the wildcard search,
        # so leaving it in place would filter the new row straight back
        # out), then explicitly select the pulled row once it's in the
        # table. _reload_videos() always resets selection to its own "most
        # recently modified" default, which usually *is* the just-pulled
        # entry but isn't guaranteed to be, so this doesn't rely on that.
        self.search_edit.clear()
        self._reload_videos()
        if self._video_root is not None:
            self._select_row_by_key(str(self._video_root / stem))

    def _select_row_by_key(self, key: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_NAME)
            if item is not None and item.data(Qt.UserRole) == key:
                self.table.selectRow(row)
                return

    def _on_pull_by_code_not_found(self) -> None:
        self._pull_worker = None
        self.search_edit.setEnabled(True)
        self._hide_status()
        _logger.info("pull by code: no such share code")
        QMessageBox.warning(self, "Not Found", "No shared video was found for that code.")

    def _on_pull_by_code_failed(self, message: str) -> None:
        self._pull_worker = None
        self.search_edit.setEnabled(True)
        self._hide_status()
        _logger.warning("pull by code failed: %s", message)
        QMessageBox.warning(self, "Sync Failed", message)

    # -- comment editor -----------------------------------------------------

    def _ensure_sequence_for(self, entry: _LibraryEntry, ffmpeg_path: str | None = None) -> Path | None:
        if entry.video_path is None:
            return entry.sequence_dir
        if video_sequence.has_sequence(entry.video_path):
            # Already extracted (a prior Comment/Mark as Share, or an old
            # pre-2026-08-20 Maya-written sequence) — nothing to do, and
            # crucially no reason to demand ffmpeg be resolvable just to
            # confirm that. Only actually needed *before* running ffmpeg.
            _logger.debug("sequence already exists for %s, skipping ffmpeg resolution", entry.video_path)
            return video_sequence.sequence_dir_for(entry.video_path)
        if ffmpeg_path is None:
            ffmpeg_path = self._resolve_ffmpeg()
            if ffmpeg_path is None:
                return None
        try:
            _logger.info("extracting sequence for %s", entry.video_path)
            self._set_status_message("Extracting frames...")
            sequence_dir = video_sequence.ensure_sequence(ffmpeg_path, entry.video_path)
            _logger.info("sequence ready at %s", sequence_dir)
            return sequence_dir
        except VideoCompressionError as exc:
            _logger.warning("sequence extraction failed for %s: %s", entry.video_path, exc)
            QMessageBox.warning(self, "Sequence Extraction Failed", str(exc))
            return None

    def _on_edit_comment_clicked(self) -> None:
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None:
            _logger.debug("Comment clicked with no selection")
            return
        self._show_status("Preparing sequence...")
        try:
            sequence_dir = self._ensure_sequence_for(entry)
            if (
                sequence_dir is not None
                and entry.share_state.get("is_shared")
                and self._api.cloud_sync is not None
                and self._project_id is not None
                and self._repo_id is not None
            ):
                # Resync this entry's comments.json from the cloud right
                # before CommentEditor opens (2026-08-21, per the user's
                # own request — "เพื่อความชัว", for certainty) — best-effort:
                # a failed sync (offline, a transient network error) just
                # means the editor opens against whatever's already local
                # rather than blocking the user from opening it at all.
                self._set_status_message("Syncing comments...")
                try:
                    pull_comments(
                        self._api.cloud_sync,
                        project_id=self._project_id,
                        repo_id=self._repo_id,
                        sequence_dir=sequence_dir,
                    )
                except Exception:  # noqa: BLE001
                    _logger.warning("Comment: failed to resync comments.json for %s", sequence_dir, exc_info=True)
        finally:
            self._hide_status()
        if sequence_dir is None:
            return
        _logger.info("opening CommentEditor for %s", sequence_dir)
        try:
            dialog = CommentEditor(sequence_dir, api=self._api, project_id=self._project_id, repo_id=self._repo_id, parent=self)
            dialog.exec()
        except Exception:
            # A crash inside CommentEditor's own __init__/exec used to fail
            # completely silently — PySide6 just prints an uncaught slot
            # exception to stderr/console and the click visibly does
            # nothing, which is indistinguishable from "the button is
            # broken" for anyone not watching a console. Surface it instead
            # (full traceback both logged and shown) so a real failure is
            # never silent again.
            _logger.exception("CommentEditor failed to open for %s", sequence_dir)
            QMessageBox.critical(
                self,
                "Comment Editor Failed",
                "Could not open the Comment Editor:\n\n{}".format(traceback.format_exc()),
            )
            return
        _logger.debug("CommentEditor closed")
        self._reload_videos()  # share state may have changed via an incremental sync during the session

    # -- share ---------------------------------------------------------------

    def _on_mark_as_share_clicked(self) -> None:
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None or self._project_id is None or self._repo_id is None:
            _logger.debug("Mark as Share clicked with no selection/active repo")
            return
        if self._api.cloud_sync is None:
            _logger.warning("Mark as Share clicked but api.cloud_sync is None")
            QMessageBox.warning(self, "Cloud Sync Unavailable", "Cloud sync isn't configured on this machine.")
            return
        if not entry.parsed:
            QMessageBox.warning(
                self,
                "Mark as Share",
                "This video's filename doesn't match the playblast naming convention, so a share code can't be "
                "generated for it.",
            )
            return
        # Shown here, through the async upload below — hidden in
        # _on_share_upload_succeeded/_failed instead of a finally in this
        # method, since the upload continues after this method returns.
        # Every early return between here and worker.start() must hide it
        # itself first.
        self._show_status("Preparing to share...")
        ffmpeg_path = None
        if entry.video_path is not None and not video_sequence.has_sequence(entry.video_path):
            # Only need ffmpeg resolvable when an extraction is actually
            # about to run — same fix as _ensure_sequence_for's own
            # has_sequence() fast path, applied here too since this method
            # also calls _resolve_ffmpeg() directly (for the fps probe
            # below, not just extraction).
            ffmpeg_path = self._resolve_ffmpeg()
            if ffmpeg_path is None:
                self._hide_status()
                return
        sequence_dir = self._ensure_sequence_for(entry, ffmpeg_path)
        if sequence_dir is None:
            self._hide_status()
            return

        # Write the final share state (code, frame_count, fps, ...) into
        # comments.json *before* anything uploads — a puller only ever
        # fetches comments.json + the exact frame_count of frames named off
        # what the pointer blob says, so both the pointer and the uploaded
        # comments.json must already agree on this data by the time the
        # pointer becomes resolvable, or a fresh pull would land with no
        # share info at all despite the frames having arrived fine.
        existing_code = entry.share_state.get("code")
        if existing_code:
            code = existing_code
        else:
            self._set_status_message("Generating share code...")
            try:
                # Checked directly against the cloud (not just trusted to
                # be unique from the random suffix alone) so a generated
                # code can never collide with one already pushed there —
                # see generate_unique_share_code's own docstring.
                code = generate_unique_share_code(
                    self._api.cloud_sync, entry.parsed["shot_code"], entry.parsed["version"]
                )
            except RuntimeError as exc:
                _logger.warning("Mark as Share: could not generate a unique share code: %s", exc)
                self._hide_status()
                QMessageBox.warning(self, "Mark as Share", str(exc))
                return
        frame_files = sorted(
            p for p in sequence_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        image_format = frame_files[0].suffix.lstrip(".") if frame_files else "png"
        if entry.video_path is not None and ffmpeg_path is not None:
            fps = video_sequence.probe_fps(ffmpeg_path, entry.video_path)
        else:
            fps = entry.share_state.get("fps") or 24.0
        # Same fixed <stem>.audio.m4a name video_sequence.ensure_sequence
        # writes to (a no-op if the source has no audio track at all) —
        # ShareUploadWorker below uploads it automatically since it just
        # pushes every file already sitting in sequence_dir, but the
        # pointer still needs to record whether it exists so a puller on
        # another machine knows whether to bother asking for it.
        audio_format = "m4a" if video_sequence.has_audio_file(sequence_dir, sequence_dir.name) else None
        comment_store.set_share_state(
            sequence_dir,
            is_shared=True,
            code=code,
            shared_at=datetime.datetime.now().isoformat(),
            frame_count=len(frame_files),
            image_format=image_format,
            fps=fps,
            audio_format=audio_format,
        )

        _logger.info("Mark as Share: uploading %s (code=%s, %d frame(s))", sequence_dir, code, len(frame_files))
        self.copy_clipboard_button.setEnabled(False)
        self._set_status_message("Uploading to cloud...")
        worker = ShareUploadWorker(
            self._api.cloud_sync, project_id=self._project_id, repo_id=self._repo_id, sequence_dir=sequence_dir, parent=self
        )
        worker.succeeded.connect(
            lambda: self._on_share_upload_succeeded(
                code, entry.key, sequence_dir, len(frame_files), image_format, fps, audio_format
            )
        )
        worker.failed.connect(
            lambda message: self._on_share_upload_failed(
                message, entry.key, sequence_dir, was_already_shared=bool(existing_code)
            )
        )
        worker.finished.connect(worker.deleteLater)
        self._share_worker = worker
        worker.start()

    def _on_share_upload_succeeded(
        self, code: str, entry_key: str, sequence_dir: Path, frame_count: int, image_format: str, fps: float,
        audio_format: str | None,
    ) -> None:
        self._share_worker = None
        self.copy_clipboard_button.setEnabled(True)
        self._hide_status()
        _logger.info("Mark as Share: upload succeeded for %s, pushing pointer", sequence_dir)
        # Only made discoverable now that every frame + the final
        # comments.json (already carrying this same share info) has
        # actually landed — see the ordering note in
        # _on_mark_as_share_clicked above.
        push_pointer(
            self._api.cloud_sync,
            code,
            project_id=self._project_id,
            repo_id=self._repo_id,
            video_stem=sequence_dir.name,
            frame_count=frame_count,
            image_format=image_format,
            fps=fps,
            audio_format=audio_format,
        )
        self._reload_videos()
        # _reload_videos() always resets selection to its own "most
        # recently modified" default (see its own docstring) — usually the
        # entry just shared, but not guaranteed to be if some other entry
        # happens to have an even more recent mtime. Explicitly re-select
        # the exact entry this share was for (fixed 2026-08-21, a real bug
        # report: without this, a later click on pushButton_copy_clipboard
        # could copy a *different* entry's code than the one this dialog
        # just showed, if selection had drifted to some other row).
        self._select_row_by_key(entry_key)
        # Single-button "Copy Code and Close" instead of a plain OK dialog
        # (2026-08-21, per the user's own request) — the whole point of
        # this dialog is handing the just-generated code to the artist, so
        # the one button does both at once rather than needing a separate
        # Copy Share Code click afterward. clickedButton() check (rather
        # than always copying once exec() returns) so dismissing via
        # Esc/the window's close button doesn't silently copy anyway.
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Information)
        dialog.setWindowTitle("Marked as Share")
        dialog.setText("Shared. Code: {}".format(code))
        copy_close_button = dialog.addButton("Copy Code and Close", QMessageBox.AcceptRole)
        dialog.exec()
        if dialog.clickedButton() is copy_close_button:
            QApplication.clipboard().setText(code)

    def _on_share_upload_failed(self, message: str, entry_key: str, sequence_dir: Path, *, was_already_shared: bool) -> None:
        self._share_worker = None
        self.copy_clipboard_button.setEnabled(True)
        self._hide_status()
        _logger.warning("Mark as Share: upload failed for %s: %s", sequence_dir, message)
        if not was_already_shared:
            # Roll back the optimistic is_shared=True set before the upload
            # started (see _on_mark_as_share_clicked) — nothing actually
            # reached the cloud, so the table shouldn't claim it's shared,
            # and Copy Clipboard shouldn't offer a code that resolves to
            # nothing.
            comment_store.set_share_state(sequence_dir, is_shared=False)
            self._reload_videos()
            self._select_row_by_key(entry_key)
        QMessageBox.warning(self, "Share Failed", message)

    def _on_copy_clipboard_clicked(self) -> None:
        """pushButton_copy_clipboard — doubles as Make Share/Copy Code
        depending on the selected entry's share state (2026-08-22, per the
        user's own request to merge the two buttons into one); which action
        applies is decided fresh at click time rather than baked into a
        separate handler, so it stays correct even if share state changed
        underneath (e.g. an incremental sync) since the last button-state
        refresh."""
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None:
            return
        if entry.share_state.get("is_shared"):
            code = entry.share_state.get("code")
            if code:
                QApplication.clipboard().setText(code)
        else:
            self._on_mark_as_share_clicked()

    # -- delete ---------------------------------------------------------------

    def _on_delete_playblast_clicked(self) -> None:
        """pushButton_delete_playblast — deletes every selected row's local
        video file + sequence_dir (comments.json included) from this
        machine only. Never calls into share_sync/cloud_sync: R2JsonSync
        only has pull/push today, no delete-blob operation, so a shared
        entry's uploaded frames/comments.json stay in the cloud (and any
        other machine that still has its share code can keep pulling it)
        regardless of what's clicked here — confirmed with the user, not a
        bug to fix later."""
        entries = self._selected_entries()
        if not entries:
            return
        shared_entries = [e for e in entries if e.share_state.get("is_shared")]
        if shared_entries:
            message = (
                "{} of {} selected playblast(s) are marked as shared.\n\n"
                "Deleting removes only your LOCAL copy — the shared copy stays available in the cloud "
                "(and to anyone who still has its share code). Removing a shared playblast from the "
                "server isn't supported yet.\n\nDelete the local copy anyway?"
            ).format(len(shared_entries), len(entries))
        else:
            message = "Delete {} selected playblast(s) from this machine? This cannot be undone.".format(len(entries))
        if QMessageBox.question(
            self, "Delete Playblast", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return

        deleted_keys = set()
        failed_names = []
        for entry in entries:
            try:
                if entry.video_path is not None and entry.video_path.is_file():
                    entry.video_path.unlink()
                if entry.sequence_dir.is_dir():
                    shutil.rmtree(entry.sequence_dir)
                deleted_keys.add(entry.key)
            except OSError:
                _logger.exception("failed to delete playblast %s", entry.key)
                failed_names.append(entry.stem)
        _logger.info("Delete Playblast: removed %d of %d selected entr(y/ies)", len(deleted_keys), len(entries))
        if self._selected_key in deleted_keys:
            self._selected_key = None
        self._reload_videos()
        if failed_names:
            QMessageBox.warning(
                self, "Delete Failed", "Could not delete: {}".format(", ".join(failed_names))
            )

    # -- get video / get video - commented -----------------------------------

    def _notify_export_ready(self, output_path: Path) -> None:
        """Shown once Get Video/Get Video - Commented finishes, before
        Explorer pops open — added 2026-08-21 per the user's own request,
        so Explorer opening on top of the app doesn't catch anyone off
        guard. Single button ("Ok and Show me in explorer" — there's no
        second choice, so a plain Yes/No or OK/Cancel pair doesn't apply)
        that both dismisses the dialog and triggers the reveal, same as
        clicking OK would for a normal info box."""
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Information)
        dialog.setWindowTitle("Export Complete")
        dialog.setText("Saved: {}".format(output_path.name))
        dialog.addButton("Ok and Show me in explorer", QMessageBox.AcceptRole)
        dialog.exec()
        _reveal_and_select_in_explorer(output_path)

    def _on_get_video_clicked(self) -> None:
        """pushButton_get_video — routes to the plain or "commented" export
        depending on checkBox_display_comment_download (2026-08-22, per the
        user's own request to merge the old separate Get Video/Get Video -
        Commented buttons behind this one checkbox)."""
        if self.display_comment_download_checkbox is not None and self.display_comment_download_checkbox.isChecked():
            self._export_commented_video()
        else:
            self._export_plain_video()

    def _export_plain_video(self) -> None:
        """Burns the current frame number into every frame (top-center,
        matching player_widget.py's own _FrameNumberOverlay HUD look) while
        also targeting _MAX_EXPORT_BYTES, in one single ffmpeg pass (see
        video_compress.burn_frame_numbers — merged from a burn-then-
        compress two-pass version 2026-08-21, which made Get Video
        noticeably slower since burning text already forces a full
        re-encode regardless of the source's own size), then copies the
        result into this repo's export folder, overwriting whatever was
        there before — regenerated fresh on every click, never reused, and
        never touched by any cloud-sync code path in this plugin (see
        video_path_store.resolve_export_dir)."""
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None or entry.video_path is None or self._project_id is None or self._repo_id is None:
            return
        self._show_status("Rendering video...")
        try:
            ffmpeg_path = self._resolve_ffmpeg()
            if ffmpeg_path is None:
                return
            export_dir = video_path_store.resolve_export_dir(self._api, self._project_id, self._repo_id)
            output_path = export_dir / "{}.mp4".format(entry.stem)
            try:
                result_path = burn_frame_numbers(ffmpeg_path, entry.video_path, _MAX_EXPORT_BYTES)
            except VideoCompressionError as exc:
                _logger.warning("Get Video failed for %s: %s", entry.video_path, exc)
                QMessageBox.warning(self, "Get Video Failed", str(exc))
                return
            shutil.copy2(result_path, output_path)
            # Always a fresh temp dir of its own (burn_frame_numbers always
            # re-encodes, unlike compress_to_fit's own "return video_path
            # unchanged" fast path) — always ours to clean up.
            shutil.rmtree(result_path.parent, ignore_errors=True)
            _logger.info("Get Video: wrote %s", output_path)
        finally:
            self._hide_status()
        self._notify_export_ready(output_path)

    def _export_commented_video(self) -> None:
        """Same 20MB-capped local export as Get Video above, but composites
        each frame's saved drawing (core/comment_store.py's per-frame
        "strokes") onto the frame first — works for a sequence-only entry
        too (no local video file needed), unlike Get Video."""
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None or self._project_id is None or self._repo_id is None:
            return
        self._show_status("Preparing sequence...")
        try:
            sequence_dir = self._ensure_sequence_for(entry)
            if sequence_dir is None:
                return
            ffmpeg_path = self._resolve_ffmpeg()
            if ffmpeg_path is None:
                return
            export_dir = video_path_store.resolve_export_dir(self._api, self._project_id, self._repo_id)
            output_path = export_dir / "{}_commented.mp4".format(entry.stem)
            try:
                self._render_commented_video(ffmpeg_path, sequence_dir, output_path)
            except VideoCompressionError as exc:
                _logger.warning("Get Video - Commented failed for %s: %s", sequence_dir, exc)
                QMessageBox.warning(self, "Get Video - Commented Failed", str(exc))
                return
            _logger.info("Get Video - Commented: wrote %s", output_path)
        finally:
            self._hide_status()
        self._notify_export_ready(output_path)

    def _render_commented_video(self, ffmpeg_path: str, sequence_dir: Path, output_path: Path) -> None:
        frame_paths = sorted(
            p for p in sequence_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        if not frame_paths:
            raise VideoCompressionError("No extracted frames found for {} — nothing to render.".format(sequence_dir.name))
        data = comment_store.load(sequence_dir)
        frames_meta = data["frames"]
        fps = data["share"].get("fps") or 24.0

        render_dir = Path(tempfile.mkdtemp(prefix="ukorehub_commented_"))
        try:
            self._set_status_message("Compositing frames...")
            # comment_store.json keys strokes by the same 0-based sorted
            # position sequence_player.py uses as its own frame_index — see
            # that file's frame_paths[current_frame_index] lookup.
            for index, frame_path in enumerate(frame_paths):
                image = QImage(str(frame_path))
                strokes = frames_meta.get(str(index), {}).get("strokes", [])
                # Painter now always runs (not just when strokes exist) —
                # the frame number is stamped on every frame regardless of
                # whether it has a saved drawing, per the user's own
                # request that Get Video - Commented burn it in the same
                # as the plain Get Video export does.
                painter = QPainter(image)
                painter.setRenderHint(QPainter.Antialiasing)
                for stroke in strokes:
                    paint_stroke_points(
                        painter,
                        [tuple(p) for p in stroke.get("points", [])],
                        QColor(stroke.get("color", "#ff3b30")),
                        int(stroke.get("width", 4)),
                        image.width(),
                        image.height(),
                    )
                paint_frame_number(painter, str(index), image.width())
                painter.end()
                image.save(str(render_dir / "frame.{:05d}.png".format(index + 1)))

            audio_path = video_sequence.audio_path_for(sequence_dir, sequence_dir.name)
            temp_output = render_dir / "rendered.mp4"
            self._set_status_message("Encoding video...")
            video_sequence.encode_sequence_to_video(
                ffmpeg_path,
                render_dir / "frame.%05d.png",
                fps,
                temp_output,
                audio_path=audio_path if audio_path.is_file() else None,
            )
            self._set_status_message("Compressing video...")
            final_path = compress_to_fit(ffmpeg_path, temp_output, _MAX_EXPORT_BYTES)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_path, output_path)
            if final_path != temp_output:
                # compress_to_fit produced a second, separate temp dir of
                # its own (temp_output was already over _MAX_EXPORT_BYTES) —
                # clean that up too, not just render_dir below.
                shutil.rmtree(final_path.parent, ignore_errors=True)
        finally:
            shutil.rmtree(render_dir, ignore_errors=True)

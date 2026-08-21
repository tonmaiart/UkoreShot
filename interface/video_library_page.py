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
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QGroupBox,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ukoreshot_plugin.core import comment_store, video_naming, video_path_store, video_sequence
from ukoreshot_plugin.core.video_compress import VideoCompressionError, compress_to_fit, get_ffmpeg_path
from ukoreshot_plugin.core.share_sync import PullByCodeWorker, ShareUploadWorker, push_pointer
from ukoreshot_plugin.interface.comment_editor import CommentEditor
from ukoreshot_plugin.interface.draw_overlay import paint_stroke_points
from ukoreshot_plugin.interface.player_widget import PlayerWidget
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

_COL_THUMBNAIL, _COL_NAME, _COL_SHARED, _COL_DATE, _COL_TIME_AGO = range(5)

_SORT_NAME_ASC = "name_asc"
_SORT_OLDEST = "oldest"
_SORT_NEWEST = "newest"
_DEFAULT_SORT = _SORT_NEWEST

# Matches comment_store.generate_share_code's exact output shape
# ({shot_code}_v{version:03d}_{4 hex chars}) — used by the search bar's
# Enter-key handler to tell "someone pasted a share code" apart from a
# plain wildcard search string, without needing a separate dedicated field.
_SHARE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]+_v\d{3}_[0-9A-Fa-f]{4}$")


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
    (Thumbnail/Name/Shared/Date/Time Ago) replaces the old FlowLayout card
    grid + FilterSidebar entirely — confirmed with the user this round that
    per-category filtering is retired in favor of just the wildcard search
    bar + sort buttons this .ui actually has.

    Video->image-sequence splitting is lazy (core/video_sequence.py),
    triggered only from pushButton_comment/pushButton_mark_as_share's own
    handlers — never from a reload/refresh scan, per the user's explicit
    "don't convert every video just from browsing" instruction. Edit
    Comment (pushButton_comment) opens the in-house CommentEditor directly
    (see comment_editor.py) — PlayerWidget no longer has its own duplicate
    Edit Comment button (removed 2026-08-21, this page's own button already
    covers it).

    Discord support removed entirely 2026-08-21 (standalone tool now) —
    pushButton_get_video/pushButton_get_video_commented replace the old
    Discord-oriented "get format video"/"auto send to Discord" buttons:
    both just generate a local .mp4 (hard-capped at 20MB) into a
    cache-only export folder that's never touched by cloud sync, and
    reveal+select it in Explorer — see _on_get_video_clicked/
    _on_get_video_commented_clicked below."""

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
        self._share_worker: ShareUploadWorker | None = None
        self._pull_worker: PullByCodeWorker | None = None
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
        self.sort_oldest_button: QPushButton = find(QPushButton, "pushButton_sort_oldest")
        self.sort_newest_button: QPushButton = find(QPushButton, "pushButton_sort_newest")
        self.comment_button: QPushButton = find(QPushButton, "pushButton_comment")
        self.mark_as_share_button: QPushButton = find(QPushButton, "pushButton_mark_as_share")
        self.copy_clipboard_button: QPushButton = find(QPushButton, "pushButton_copy_clipboard")
        self.get_video_button: QPushButton = find(QPushButton, "pushButton_get_video")
        self.get_video_commented_button: QPushButton = find(QPushButton, "pushButton_get_video_commented")
        self.prev_frame_button: QPushButton = find(QPushButton, "pushButton_previous_frame")
        self.play_button: QPushButton = find(QPushButton, "pushButton_play")
        self.next_frame_button: QPushButton = find(QPushButton, "pushButton_next_frame")
        self.prev_comment_button: QPushButton = find(QPushButton, "pushButton_previous_comment")
        self.next_comment_button: QPushButton = find(QPushButton, "pushButton_next_comment")
        self.speed_combo: QComboBox = find(QComboBox, "comboBox_speed")
        self.keyframe_edit: QLineEdit = find(QLineEdit, "lineEdit_keyframe")
        # Found so a missing-widget error still logs if it's ever removed,
        # but left unwired — its intended behavior (toggle what, exactly?)
        # hasn't been specified yet; ask before building it.
        self.show_comment_toggle_button: QPushButton = find(QPushButton, "pushButton_show_comment_toggle")

        for _name, _widget in [
            ("groupBox_playblast_viewer", self.viewer_group),
            ("tableWidget_playblast_library", self.table),
            ("lineEdit_search_bar", self.search_edit),
            ("pushButton_reload", self.reload_button),
            ("pushButton_sort_ascending", self.sort_ascending_button),
            ("pushButton_sort_oldest", self.sort_oldest_button),
            ("pushButton_sort_newest", self.sort_newest_button),
            ("pushButton_comment", self.comment_button),
            ("pushButton_mark_as_share", self.mark_as_share_button),
            ("pushButton_copy_clipboard", self.copy_clipboard_button),
            ("pushButton_get_video", self.get_video_button),
            ("pushButton_get_video_commented", self.get_video_commented_button),
            ("pushButton_previous_frame", self.prev_frame_button),
            ("pushButton_play", self.play_button),
            ("pushButton_next_frame", self.next_frame_button),
            ("pushButton_previous_comment", self.prev_comment_button),
            ("pushButton_next_comment", self.next_comment_button),
            ("comboBox_speed", self.speed_combo),
            ("lineEdit_keyframe", self.keyframe_edit),
            ("pushButton_show_comment_toggle", self.show_comment_toggle_button),
        ]:
            if _widget is None:
                _logger.error("UkoreShotPage.ui has no widget named %r — findChild returned None", _name)

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

        # 0.25x-1.00x discrete steps — comboBox_speed replaces the old
        # continuous speed_slider for this page only (CommentEditor keeps
        # its own slider, no .ui equivalent there).
        self.speed_combo.addItems(["0.25x", "0.5x", "0.75x", "1.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self._on_speed_combo_changed)

        self.keyframe_edit.setPlaceholderText("Frame")
        self.keyframe_edit.returnPressed.connect(self._on_keyframe_edit_entered)

        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["", "Name", "Shared", "Date", "Time Ago"])
        self.table.setIconSize(_ICON_SIZE)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.verticalHeader().setDefaultSectionSize(_ICON_SIZE.height() + 12)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_THUMBNAIL, QHeaderView.Fixed)
        self.table.setColumnWidth(_COL_THUMBNAIL, _ICON_SIZE.width() + 8)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_SHARED, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_DATE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_TIME_AGO, QHeaderView.ResizeToContents)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.returnPressed.connect(self._on_search_enter)
        self.reload_button.clicked.connect(self._reload_videos)
        self.sort_ascending_button.clicked.connect(lambda: self._set_sort_mode(_SORT_NAME_ASC))
        self.sort_oldest_button.clicked.connect(lambda: self._set_sort_mode(_SORT_OLDEST))
        self.sort_newest_button.clicked.connect(lambda: self._set_sort_mode(_SORT_NEWEST))
        self.comment_button.clicked.connect(self._on_edit_comment_clicked)
        self.mark_as_share_button.clicked.connect(self._on_mark_as_share_clicked)
        self.copy_clipboard_button.clicked.connect(self._on_copy_clipboard_clicked)
        self.get_video_button.clicked.connect(self._on_get_video_clicked)
        self.get_video_commented_button.clicked.connect(self._on_get_video_commented_clicked)
        self.copy_clipboard_button.setEnabled(False)

        self._update_button_states()
        _logger.info("UkoreShotPage.__init__ finished")

    # -- standard page protocol -------------------------------------------

    def set_repo(self, project, repo, workspace_root: str) -> None:
        self._project_id = project.id if project is not None else None
        self._repo_id = repo.id if repo is not None else None
        _logger.info("set_repo(project_id=%s, repo_id=%s)", self._project_id, self._repo_id)
        self._reload_videos()

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

    # -- video list ---------------------------------------------------------

    def _resolve_ffmpeg(self) -> str | None:
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

    def _set_sort_mode(self, mode: str) -> None:
        self._sort_mode = mode
        self._apply_filter()

    def _sort_entries(self, entries: list[_LibraryEntry]) -> list[_LibraryEntry]:
        if self._sort_mode == _SORT_NAME_ASC:
            return sorted(entries, key=lambda e: e.stem.lower())
        if self._sort_mode == _SORT_OLDEST:
            return sorted(entries, key=lambda e: e.mtime)
        return sorted(entries, key=lambda e: e.mtime, reverse=True)  # _SORT_NEWEST, the default

    def _apply_filter(self) -> None:
        search = self.search_edit.text().strip()
        entries = [e for e in self._entries_by_key.values() if _wildcard_match(search, e.stem)]
        entries = self._sort_entries(entries)

        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.stem)
            name_item.setData(Qt.UserRole, entry.key)
            self.table.setItem(row, _COL_THUMBNAIL, QTableWidgetItem())
            self.table.setItem(row, _COL_NAME, name_item)
            shared_item = QTableWidgetItem("Shared" if entry.share_state["is_shared"] else "—")
            self.table.setItem(row, _COL_SHARED, shared_item)
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
            return
        if entry.video_path is not None:
            self.player_widget.load_video(entry.video_path)
        else:
            self.player_widget.load_sequence(entry.sequence_dir)
        # Scrubber's own red comment-frame marks, added 2026-08-21 —
        # reads straight off comments.json, same as Previous/Next Comment,
        # so this never forces a sequence extraction just to show them.
        self.player_widget.set_comment_frames(self._selected_entry_keyframe_indices())

    def _update_empty_state(self) -> None:
        # No dedicated empty-state widget in the new .ui (unlike the old
        # empty_label/content_widget split) — an empty table + a cleared
        # player already communicate "nothing here yet" on its own.
        pass

    def _update_button_states(self) -> None:
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        has_selection = entry is not None
        self.comment_button.setEnabled(has_selection)
        self.mark_as_share_button.setEnabled(has_selection)
        # Get Video needs a real source file to compress from; Get Video -
        # Commented works off the sequence instead, which every selectable
        # entry has (or can lazily extract) regardless of a local video file.
        self.get_video_button.setEnabled(has_selection and entry.video_path is not None)
        self.get_video_commented_button.setEnabled(has_selection)
        is_shared = has_selection and entry.share_state["is_shared"]
        self.copy_clipboard_button.setEnabled(bool(is_shared and entry.share_state.get("code")))

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
        _logger.info("pull by code succeeded: %s", stem)
        self._reload_videos()
        QMessageBox.information(self, "Video Synced", "Pulled \"{}\" down from the cloud.".format(stem))

    def _on_pull_by_code_not_found(self) -> None:
        self._pull_worker = None
        self.search_edit.setEnabled(True)
        _logger.info("pull by code: no such share code")
        QMessageBox.warning(self, "Not Found", "No shared video was found for that code.")

    def _on_pull_by_code_failed(self, message: str) -> None:
        self._pull_worker = None
        self.search_edit.setEnabled(True)
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
        sequence_dir = self._ensure_sequence_for(entry)
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
        ffmpeg_path = None
        if entry.video_path is not None and not video_sequence.has_sequence(entry.video_path):
            # Only need ffmpeg resolvable when an extraction is actually
            # about to run — same fix as _ensure_sequence_for's own
            # has_sequence() fast path, applied here too since this method
            # also calls _resolve_ffmpeg() directly (for the fps probe
            # below, not just extraction).
            ffmpeg_path = self._resolve_ffmpeg()
            if ffmpeg_path is None:
                return
        sequence_dir = self._ensure_sequence_for(entry, ffmpeg_path)
        if sequence_dir is None:
            return

        # Write the final share state (code, frame_count, fps, ...) into
        # comments.json *before* anything uploads — a puller only ever
        # fetches comments.json + the exact frame_count of frames named off
        # what the pointer blob says, so both the pointer and the uploaded
        # comments.json must already agree on this data by the time the
        # pointer becomes resolvable, or a fresh pull would land with no
        # share info at all despite the frames having arrived fine.
        existing_code = entry.share_state.get("code")
        code = existing_code or comment_store.generate_share_code(entry.parsed["shot_code"], entry.parsed["version"])
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
        self.mark_as_share_button.setEnabled(False)
        worker = ShareUploadWorker(
            self._api.cloud_sync, project_id=self._project_id, repo_id=self._repo_id, sequence_dir=sequence_dir, parent=self
        )
        worker.succeeded.connect(
            lambda: self._on_share_upload_succeeded(code, sequence_dir, len(frame_files), image_format, fps, audio_format)
        )
        worker.failed.connect(lambda message: self._on_share_upload_failed(message, sequence_dir, was_already_shared=bool(existing_code)))
        worker.finished.connect(worker.deleteLater)
        self._share_worker = worker
        worker.start()

    def _on_share_upload_succeeded(self, code: str, sequence_dir: Path, frame_count: int, image_format: str, fps: float, audio_format: str | None) -> None:
        self._share_worker = None
        self.mark_as_share_button.setEnabled(True)
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
        QMessageBox.information(self, "Marked as Share", "Shared. Code: {}".format(code))

    def _on_share_upload_failed(self, message: str, sequence_dir: Path, *, was_already_shared: bool) -> None:
        self._share_worker = None
        self.mark_as_share_button.setEnabled(True)
        _logger.warning("Mark as Share: upload failed for %s: %s", sequence_dir, message)
        if not was_already_shared:
            # Roll back the optimistic is_shared=True set before the upload
            # started (see _on_mark_as_share_clicked) — nothing actually
            # reached the cloud, so the table shouldn't claim it's shared,
            # and Copy Clipboard shouldn't offer a code that resolves to
            # nothing.
            comment_store.set_share_state(sequence_dir, is_shared=False)
            self._reload_videos()
        QMessageBox.warning(self, "Share Failed", message)

    def _on_copy_clipboard_clicked(self) -> None:
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None:
            return
        code = entry.share_state.get("code")
        if code:
            QApplication.clipboard().setText(code)

    # -- get video / get video - commented -----------------------------------

    def _on_get_video_clicked(self) -> None:
        """Compresses the selected video's own source file down to
        _MAX_EXPORT_BYTES via ffmpeg (a no-op transcode if it's already
        under that) and copies the result into this repo's export folder,
        overwriting whatever was there before — regenerated fresh on every
        click, never reused, and never touched by any cloud-sync code path
        in this plugin (see video_path_store.resolve_export_dir)."""
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None or entry.video_path is None or self._project_id is None or self._repo_id is None:
            return
        ffmpeg_path = self._resolve_ffmpeg()
        if ffmpeg_path is None:
            return
        export_dir = video_path_store.resolve_export_dir(self._api, self._project_id, self._repo_id)
        output_path = export_dir / "{}.mp4".format(entry.stem)
        try:
            result_path = compress_to_fit(ffmpeg_path, entry.video_path, _MAX_EXPORT_BYTES)
        except VideoCompressionError as exc:
            _logger.warning("Get Video failed for %s: %s", entry.video_path, exc)
            QMessageBox.warning(self, "Get Video Failed", str(exc))
            return
        shutil.copy2(result_path, output_path)
        if result_path != entry.video_path:
            shutil.rmtree(result_path.parent, ignore_errors=True)
        _logger.info("Get Video: wrote %s", output_path)
        _reveal_and_select_in_explorer(output_path)

    def _on_get_video_commented_clicked(self) -> None:
        """Same 20MB-capped local export as Get Video above, but composites
        each frame's saved drawing (core/comment_store.py's per-frame
        "strokes") onto the frame first — works for a sequence-only entry
        too (no local video file needed), unlike Get Video."""
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None or self._project_id is None or self._repo_id is None:
            return
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
        _reveal_and_select_in_explorer(output_path)

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
            # comment_store.json keys strokes by the same 0-based sorted
            # position sequence_player.py uses as its own frame_index — see
            # that file's frame_paths[current_frame_index] lookup.
            for index, frame_path in enumerate(frame_paths):
                image = QImage(str(frame_path))
                strokes = frames_meta.get(str(index), {}).get("strokes", [])
                if strokes:
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
                    painter.end()
                image.save(str(render_dir / "frame.{:05d}.png".format(index + 1)))

            audio_path = video_sequence.audio_path_for(sequence_dir, sequence_dir.name)
            temp_output = render_dir / "rendered.mp4"
            video_sequence.encode_sequence_to_video(
                ffmpeg_path,
                render_dir / "frame.%05d.png",
                fps,
                temp_output,
                audio_path=audio_path if audio_path.is_file() else None,
            )
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

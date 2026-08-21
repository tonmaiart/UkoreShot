from __future__ import annotations

import datetime
import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFile, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# open_in_file_explorer: the documented plugin_api re-export of
# core/os_utils.py's helper (see developer/app/docs/plugin-api.md's "Misc
# helpers" row under "Re-exported core/ types") — the sanctioned way to
# reach it from a plugin, not a direct `core.os_utils` import.
from plugin_api import open_in_file_explorer
from ukoreshot_plugin.core import comment_store, discord_client, video_naming, video_path_store, video_sequence
from ukoreshot_plugin.core.video_compress import VideoCompressionError, compress_to_fit
from ukoreshot_plugin.core.share_sync import PullByCodeWorker, ShareUploadWorker, push_pointer
from ukoreshot_plugin.interface.comment_editor import CommentEditor
from ukoreshot_plugin.interface.discord_send_worker import DiscordSendWorker
from ukoreshot_plugin.interface.player_widget import PlayerWidget
from ukoreshot_plugin.interface.thumbnail_loader import ThumbnailLoader

_logger = logging.getLogger("UkoreShot.Library")

_UI_FILE = Path(__file__).resolve().parent / "UkoreShotPage.ui"
_VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi"}
_ICON_SIZE = QSize(96, 60)

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
    "don't convert every video just from browsing" instruction. The old
    BananaSketch hand-off for Edit Comment is gone — pushButton_comment (via
    PlayerWidget.editCommentRequested) now opens the in-house CommentEditor
    directly (see comment_editor.py), reviving the draw/comment system that
    was extracted out of this plugin on 2026-08-08."""

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
        self._discord_worker: DiscordSendWorker | None = None
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
        self.get_format_video_button: QPushButton = find(QPushButton, "pushButton_get_format_video")
        self.auto_send_discord_button: QPushButton = find(QPushButton, "pushButton_auto_send_to_discord")

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
            ("pushButton_get_format_video", self.get_format_video_button),
            ("pushButton_auto_send_to_discord", self.auto_send_discord_button),
        ]:
            if _widget is None:
                _logger.error("UkoreShotPage.ui has no widget named %r — findChild returned None", _name)

        # Player lives inside the .ui's own empty placeholder groupbox —
        # same "insert a real widget into a Designer-authored empty
        # QGroupBox at runtime" convention groupBox_playblast_viewer in
        # comment_editor.py's CommentEditor.ui also uses.
        self.player_widget = PlayerWidget()
        self.player_widget.editCommentRequested.connect(self._on_edit_comment_clicked)
        self.player_widget.sendToDiscordRequested.connect(self._on_send_discord_clicked)
        viewer_layout = QVBoxLayout(self.viewer_group)
        viewer_layout.setContentsMargins(4, 16, 4, 4)
        viewer_layout.addWidget(self.player_widget)

        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["", "Name", "Shared", "Date", "Time Ago"])
        self.table.setIconSize(_ICON_SIZE)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_row_selected)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.returnPressed.connect(self._on_search_enter)
        self.reload_button.clicked.connect(self._reload_videos)
        self.sort_ascending_button.clicked.connect(lambda: self._set_sort_mode(_SORT_NAME_ASC))
        self.sort_oldest_button.clicked.connect(lambda: self._set_sort_mode(_SORT_OLDEST))
        self.sort_newest_button.clicked.connect(lambda: self._set_sort_mode(_SORT_NEWEST))
        self.comment_button.clicked.connect(self._on_edit_comment_clicked)
        self.mark_as_share_button.clicked.connect(self._on_mark_as_share_clicked)
        self.copy_clipboard_button.clicked.connect(self._on_copy_clipboard_clicked)
        self.get_format_video_button.clicked.connect(self._on_get_format_video_clicked)
        self.auto_send_discord_button.clicked.connect(self._on_send_discord_clicked)
        self.copy_clipboard_button.setEnabled(False)

        self._update_button_states()
        _logger.info("UkoreShotPage.__init__ finished")

    # -- standard page protocol -------------------------------------------

    def set_repo(self, project, repo, workspace_root: str) -> None:
        self._project_id = project.id if project is not None else None
        self._repo_id = repo.id if repo is not None else None
        _logger.info("set_repo(project_id=%s, repo_id=%s)", self._project_id, self._repo_id)
        self._reload_videos()

    # -- video list ---------------------------------------------------------

    def _resolve_ffmpeg(self) -> str | None:
        try:
            path = video_sequence.resolve_ffmpeg_path(discord_client.get_ffmpeg_path(self._api))
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
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None:
            self.player_widget.clear_video()
            return
        if entry.video_path is not None:
            self.player_widget.load_video(entry.video_path)
        else:
            self.player_widget.load_sequence(entry.sequence_dir)

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
        self.get_format_video_button.setEnabled(has_selection and entry.video_path is not None)
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
        dialog = CommentEditor(sequence_dir, api=self._api, project_id=self._project_id, repo_id=self._repo_id, parent=self)
        dialog.exec()
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
        comment_store.set_share_state(
            sequence_dir,
            is_shared=True,
            code=code,
            shared_at=datetime.datetime.now().isoformat(),
            frame_count=len(frame_files),
            image_format=image_format,
            fps=fps,
        )

        _logger.info("Mark as Share: uploading %s (code=%s, %d frame(s))", sequence_dir, code, len(frame_files))
        self.mark_as_share_button.setEnabled(False)
        worker = ShareUploadWorker(
            self._api.cloud_sync, project_id=self._project_id, repo_id=self._repo_id, sequence_dir=sequence_dir, parent=self
        )
        worker.succeeded.connect(
            lambda: self._on_share_upload_succeeded(code, sequence_dir, len(frame_files), image_format, fps)
        )
        worker.failed.connect(lambda message: self._on_share_upload_failed(message, sequence_dir, was_already_shared=bool(existing_code)))
        worker.finished.connect(worker.deleteLater)
        self._share_worker = worker
        worker.start()

    def _on_share_upload_succeeded(self, code: str, sequence_dir: Path, frame_count: int, image_format: str, fps: float) -> None:
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

    # -- discord ---------------------------------------------------------------

    def _on_get_format_video_clicked(self) -> None:
        """Compresses the selected video down to the repo's configured
        Discord Max Upload Size via ffmpeg and reveals it in the OS file
        explorer — a manual preview/export step, distinct from Auto Send to
        Discord Post below (which posts it automatically)."""
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None or entry.video_path is None or self._project_id is None or self._repo_id is None:
            return
        ffmpeg_path = self._resolve_ffmpeg()
        if ffmpeg_path is None:
            return
        max_upload_bytes = discord_client.get_max_upload_mb(self._api, self._project_id, self._repo_id) * 1024 * 1024
        try:
            output_path = compress_to_fit(ffmpeg_path, entry.video_path, max_upload_bytes)
        except VideoCompressionError as exc:
            _logger.warning("Get Format Video failed for %s: %s", entry.video_path, exc)
            QMessageBox.warning(self, "Get Format Video Failed", str(exc))
            return
        _logger.info("Get Format Video: revealing %s", output_path)
        open_in_file_explorer(output_path)

    def _on_send_discord_clicked(self) -> None:
        entry = self._entries_by_key.get(self._selected_key) if self._selected_key else None
        if entry is None or entry.video_path is None or self._project_id is None or self._repo_id is None:
            return
        video_path = entry.video_path
        if not entry.parsed:
            QMessageBox.warning(
                self,
                "Send to Discord",
                "This video's filename doesn't match the playblast naming convention, so its shot code can't be "
                "determined — Send to Discord needs one to find or create the matching forum post.",
            )
            return
        shot_title = entry.parsed["shot_code"]

        channel_id = discord_client.get_channel_id(self._api, self._project_id, self._repo_id)
        if not channel_id:
            QMessageBox.warning(
                self,
                "Discord Not Configured",
                "No Discord forum channel is set for this repo — set one under Repository Setting > UkoreShot first.",
            )
            return
        token = discord_client.get_bot_token(self._api, self._project_id, self._repo_id)
        if not token:
            QMessageBox.warning(
                self,
                "Discord Not Configured",
                "No Discord bot token is set for this repo — set one under Repository Setting > UkoreShot first.",
            )
            return

        max_upload_bytes = discord_client.get_max_upload_mb(self._api, self._project_id, self._repo_id) * 1024 * 1024
        ffmpeg_path = discord_client.get_ffmpeg_path(self._api)

        _logger.info("Send to Discord: sending %s (shot=%s)", video_path, shot_title)
        self.auto_send_discord_button.setEnabled(False)
        self.player_widget.send_discord_button.setEnabled(False)
        self._discord_worker = DiscordSendWorker(
            token,
            channel_id,
            shot_title,
            video_path,
            video_path.name,
            max_upload_bytes=max_upload_bytes,
            ffmpeg_path=ffmpeg_path,
            parent=self,
        )
        self._discord_worker.succeeded.connect(self._on_discord_send_succeeded)
        self._discord_worker.failed.connect(self._on_discord_send_failed)
        self._discord_worker.finished.connect(self._discord_worker.deleteLater)
        self._discord_worker.start()

    def _on_discord_send_succeeded(self) -> None:
        self._discord_worker = None
        self.auto_send_discord_button.setEnabled(True)
        self.player_widget.send_discord_button.setEnabled(True)
        _logger.info("Send to Discord succeeded")
        QMessageBox.information(self, "Sent to Discord", "Video posted to Discord.")

    def _on_discord_send_failed(self, message: str) -> None:
        self._discord_worker = None
        self.auto_send_discord_button.setEnabled(True)
        self.player_widget.send_discord_button.setEnabled(True)
        _logger.warning("Send to Discord failed: %s", message)
        QMessageBox.warning(self, "Discord Send Failed", message)

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from interface.section_registry import SectionHost
from interface.shared.widget_helpers import show_exclusive, wrap_scrollable
from ukoreshot_plugin.core import discord_client, video_naming, video_path_store
from ukoreshot_plugin.interface.discord_send_worker import DiscordSendWorker
from ukoreshot_plugin.interface.filter_sidebar import FilterSidebar
from ukoreshot_plugin.interface.flow_layout import FlowLayout
from ukoreshot_plugin.interface.player_widget import PlayerWidget
from ukoreshot_plugin.interface.thumbnail_loader import ThumbnailLoader

_VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi"}
_UNKNOWN = "Unknown"
# Must match core/theme.py's QFrame#videoCard border-radius so the painted
# thumbnail's clip path lines up with the QSS-drawn card border.
_CARD_CORNER_RADIUS = 6.0

# This plugin's own images/ folder, not the shared data/icons/ every other
# plugin uses — same _ICONS_DIR convention as player_widget.py (one parent
# up from this file is UkoreShot/ itself). Icon-setting reuses
# PlayerWidget._set_button_icon (imported below) instead of duplicating it.
_ICONS_DIR = Path(__file__).resolve().parents[1] / "images"
_SORT_AZ_ICON_PATH = _ICONS_DIR / "icons8-alphabetical-sorting-50.png"
_SORT_ZA_ICON_PATH = _ICONS_DIR / "icons8-alphabetical-sorting-2-50.png"
_SORT_OLDEST_ICON_PATH = _ICONS_DIR / "icons8-time-machine-32.png"
_SORT_NEWEST_ICON_PATH = _ICONS_DIR / "icons8-delivery-time-32.png"
_VIEW_SMALL_ICON_PATH = _ICONS_DIR / "icons8-grid-50.png"
_VIEW_LARGE_ICON_PATH = _ICONS_DIR / "icons8-grid-2-24.png"

# Two view-mode presets (view_small_button/view_large_button, added
# 2026-07-20) — _VideoCard used to hard-code these as module constants;
# now takes them as constructor args so switching the toggle can rebuild
# the grid at a different size.
_CARD_SIZES = {
    "small": {"card_width": 110, "thumbnail_height": 52},
    "large": {"card_width": 170, "thumbnail_height": 84},
}
_DEFAULT_CARD_SIZE = "large"

_SORT_NAME_ASC = "name_asc"
_SORT_NAME_DESC = "name_desc"
_SORT_OLDEST = "oldest"
_SORT_NEWEST = "newest"
_DEFAULT_SORT = _SORT_NEWEST

# video_naming.parse_video_filename's dict keys, in filter_sidebar.py's
# category order — used to build FilterSidebar.set_available_values'
# input and to test a parsed video against the sidebar's selections in
# _video_matches_filters.
_NAMING_FILTER_FIELDS = ["sequence", "shot_code", "variation", "index", "version"]


def _format_filter_value(field: str, parsed) -> str:
    """The filter sidebar shows index/version the same zero-padded way
    they actually appear in the filename (e.g. "003", "v001") rather than
    plain ints — this formatting must stay identical between
    _collect_filter_values (building the list of choices) and
    _video_matches_filters (matching a selection against it), since a
    selection is compared by exact string."""
    if parsed is None:
        return _UNKNOWN
    value = parsed[field]
    if field == "index":
        return "{:03d}".format(value)
    if field == "version":
        return "v{:03d}".format(value)
    return str(value)


class _VideoCard(QFrame):
    """One clickable card per video, painted with a fill-cropped thumbnail
    (reusing core/theme.py's card idiom as QFrame#videoCard) — replaces a
    plain QListWidget IconMode list, which rendered badly (overlapping
    thumbnails, cut-off text) for anything beyond a small square icon.
    The thumbnail only fills a fixed-height strip at the top, with the
    video's relative path underneath as normal child labels, so paintEvent
    draws the QSS background/border first and only overlays the thumbnail
    on top of that top strip — no transparent-background trick needed."""

    clicked = Signal()

    def __init__(self, video_path: Path, *, video_root: Path, card_width: int, thumbnail_height: int, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.setObjectName("videoCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self._pixmap: QPixmap | None = None
        self._thumbnail_height = thumbnail_height

        self.setFixedWidth(card_width)

        relative = video_path.relative_to(video_root)
        name_label = QLabel(relative.name)
        name_label.setWordWrap(True)
        name_label.setProperty("cardTitle", True)

        folder = str(relative.parent)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(8, 6, 8, 6)
        text_layout.setSpacing(2)
        text_layout.addWidget(name_label)
        if folder != ".":
            folder_label = QLabel(folder)
            folder_label.setProperty("secondary", True)
            folder_label.setWordWrap(True)
            text_layout.addWidget(folder_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addSpacing(self._thumbnail_height)
        layout.addLayout(text_layout)

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Clip against the whole card's rounded rect (not just the
        # thumbnail strip) so the top two corners come out rounded to match
        # the card, while the strip's bottom edge — well below the corner
        # radius — stays a plain straight line against the text area.
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(self.rect()), _CARD_CORNER_RADIUS, _CARD_CORNER_RADIUS)
        painter.setClipPath(clip_path)
        thumb_rect = QRect(0, 0, self.width(), self._thumbnail_height)
        scaled = self._pixmap.scaled(thumb_rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width() - thumb_rect.width()) // 2)
        y = max(0, (scaled.height() - thumb_rect.height()) // 2)
        painter.drawPixmap(thumb_rect, scaled, QRect(x, y, thumb_rect.width(), thumb_rect.height()))
        painter.end()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class UkoreShotPage(QWidget):
    """The UkoreShot sidebar tab's page — a plain PlayerWidget (top half,
    playback only — no drawing/comment capability of its own at all;
    "Edit Comment" opens BananaSketch instead, a separate plugin, via
    `SectionHost.navigate_and_focus` — see `set_host`/
    `_on_edit_comment_clicked` and this plugin's own `plugin.py`) plus a
    video library (bottom half) for picking which video to play.
    `content_layout` gives player_panel/library_panel
    equal `stretch=1` so the two always split available height 50/50,
    confirmed with the user 2026-07-20. The library itself is
    `filter_sidebar` (left, `FilterSidebar` — see that file) next to
    `library_content` (right: a sort/view controls row, then
    `cards_layout`, a `FlowLayout` wrapping grid of `_VideoCard`s that
    scrolls vertically). Implements the standard set_repo() page protocol
    (interface/main_window.py's _apply_to_current_page/_set_active_repo)
    so it re-resolves its video root whenever the active repo changes or
    this tab regains focus."""

    def __init__(self, parent=None, *, api):
        super().__init__(parent)
        self._api = api
        self._project_id: str | None = None
        self._repo_id: str | None = None
        self._video_root: Path | None = None
        self._all_videos: list[Path] = []
        self._parsed_by_video: dict[Path, dict | None] = {}
        self._cards: dict[str, _VideoCard] = {}
        self._selected_card: _VideoCard | None = None
        # Survives _clear_cards' teardown (unlike _selected_card, a
        # per-rebuild widget reference) so a filter/sort/view-size change
        # can restore the same video's selection instead of losing it —
        # see _restore_or_default_selection.
        self._selected_video_path: Path | None = None
        self._discord_worker: DiscordSendWorker | None = None
        # Set once via set_host (plugin.py's _wire, called at app startup)
        # — the SectionHost this page uses to open BananaSketch for Edit
        # Comment (see _on_edit_comment_clicked). None only in the brief
        # window before wiring runs, which no user-triggered code path can
        # reach in practice.
        self._host: SectionHost | None = None
        self._sort_mode = _DEFAULT_SORT
        self._card_size_mode = _DEFAULT_CARD_SIZE
        self._thumbnail_loader = ThumbnailLoader(self)
        self._thumbnail_loader.thumbnailReady.connect(self._on_thumbnail_ready)

        self.empty_label = QLabel("Select a repo to see this information.")
        self.empty_label.setWordWrap(True)

        self.filter_sidebar = FilterSidebar()
        self.filter_sidebar.filtersChanged.connect(self._apply_filter)

        # Wrapping grid — FlowLayout packs cards left-to-right and wraps to
        # a new row once it runs out of width, so the strip grows downward
        # (vertical scroll) instead of sideways.
        self.cards_container = QWidget()
        self.cards_layout = FlowLayout(self.cards_container, spacing=8)
        self.cards_scroll = wrap_scrollable(self.cards_container)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._reload_videos)
        self.list_empty_label = QLabel("No videos found yet.")
        self.list_empty_label.setWordWrap(True)
        self.list_empty_label.setProperty("secondary", True)

        # Sort buttons (added 2026-07-20 per the user's own request) —
        # four checkable QToolButtons in one exclusive QButtonGroup rather
        # than a QComboBox, matching "ปุ่ม sort by a-z, z-a, oldest, newest"
        # (explicitly "buttons") literally. QToolButton (not QPushButton)
        # so core/theme.py's existing QToolButton:checked rule (accent
        # background) shows which single choice is active, same as
        # player_widget.py's brush/eraser/text toolbox — added 2026-08-03
        # per the user's own request for a colored toggle state.
        self.sort_az_button = QToolButton()
        PlayerWidget._set_button_icon(self.sort_az_button, _SORT_AZ_ICON_PATH, "A-Z")
        self.sort_za_button = QToolButton()
        PlayerWidget._set_button_icon(self.sort_za_button, _SORT_ZA_ICON_PATH, "Z-A")
        self.sort_oldest_button = QToolButton()
        PlayerWidget._set_button_icon(self.sort_oldest_button, _SORT_OLDEST_ICON_PATH, "Oldest")
        self.sort_newest_button = QToolButton()
        PlayerWidget._set_button_icon(self.sort_newest_button, _SORT_NEWEST_ICON_PATH, "Newest")
        self._sort_buttons = {
            _SORT_NAME_ASC: self.sort_az_button,
            _SORT_NAME_DESC: self.sort_za_button,
            _SORT_OLDEST: self.sort_oldest_button,
            _SORT_NEWEST: self.sort_newest_button,
        }
        self._sort_button_group = QButtonGroup(self)
        self._sort_button_group.setExclusive(True)
        for mode, button in self._sort_buttons.items():
            button.setCheckable(True)
            self._sort_button_group.addButton(button)
            button.clicked.connect(lambda checked, m=mode: self._set_sort_mode(m))
        self._sort_buttons[_DEFAULT_SORT].setChecked(True)

        # View-mode (thumbnail size) buttons — same exclusive-button-group
        # shape as the sort buttons above, per the user's own "ปุ่ม view
        # แบบต่างๆ เช่น thumbnail เล็ก, thumbnail ใหญ่" request. QToolButton
        # for the same checked-color reasoning as the sort buttons.
        self.view_small_button = QToolButton()
        PlayerWidget._set_button_icon(self.view_small_button, _VIEW_SMALL_ICON_PATH, "Small")
        self.view_large_button = QToolButton()
        PlayerWidget._set_button_icon(self.view_large_button, _VIEW_LARGE_ICON_PATH, "Large")
        self._view_buttons = {"small": self.view_small_button, "large": self.view_large_button}
        self._view_button_group = QButtonGroup(self)
        self._view_button_group.setExclusive(True)
        for mode, button in self._view_buttons.items():
            button.setCheckable(True)
            self._view_button_group.addButton(button)
            button.clicked.connect(lambda checked, m=mode: self._set_card_size_mode(m))
        self._view_buttons[_DEFAULT_CARD_SIZE].setChecked(True)

        controls_row = QHBoxLayout()
        controls_row.addWidget(self.refresh_button)
        controls_row.addWidget(self.sort_az_button)
        controls_row.addWidget(self.sort_za_button)
        controls_row.addWidget(self.sort_oldest_button)
        controls_row.addWidget(self.sort_newest_button)
        controls_row.addStretch()
        controls_row.addWidget(self.view_small_button)
        controls_row.addWidget(self.view_large_button)

        self.library_title = QLabel("Playblast Library")
        self.library_title.setObjectName("ukoreShotSectionTitle")

        # filter_sidebar now lays out its six categories in a horizontal
        # row (see filter_sidebar.py) and sits as its own row above
        # controls_row (changed 2026-08-03 per the user's own request) —
        # library_panel used to be an HBox split between filter_sidebar
        # (left) and this content (right); that split is gone, everything
        # is one vertical stack now.
        library_panel = QWidget()
        library_panel_layout = QVBoxLayout(library_panel)
        library_panel_layout.setContentsMargins(0, 0, 0, 0)
        library_panel_layout.addWidget(self.library_title)
        library_panel_layout.addWidget(self.filter_sidebar)
        library_panel_layout.addLayout(controls_row)
        library_panel_layout.addWidget(self.cards_scroll, stretch=1)
        library_panel_layout.addWidget(self.list_empty_label)

        # Edit Comment lives inside PlayerWidget itself as a square icon
        # button — PlayerWidget tracks its own enabled state
        # (load_video/clear_video) since it already knows whether a video
        # is loaded; this page just needs to know *which* video to open
        # when the signal fires, via _selected_card (set in _select_card,
        # always alongside load_video), and now (2026-08-08) opens
        # BananaSketch via SectionHost.navigate_and_focus instead of an
        # in-app EditVideoDialog — see set_host/_on_edit_comment_clicked.
        self.player_widget = PlayerWidget()
        self.player_widget.editCommentRequested.connect(self._on_edit_comment_clicked)
        self.player_widget.sendToDiscordRequested.connect(self._on_send_discord_clicked)

        self.player_title = QLabel("Playblast Viewer")
        self.player_title.setObjectName("ukoreShotSectionTitle")

        player_panel = QWidget()
        player_layout = QVBoxLayout(player_panel)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.addWidget(self.player_title)
        player_layout.addWidget(self.player_widget, stretch=1)

        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(player_panel, stretch=1)
        content_layout.addWidget(library_panel, stretch=1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.content_widget)

        self._update_empty_state()

    # -- standard page protocol -------------------------------------------

    def set_host(self, host: SectionHost) -> None:
        """Called once from plugin.py's _wire (SectionSpec.wire, run at app
        startup) — see _on_edit_comment_clicked for the one thing this
        page uses it for."""
        self._host = host

    def set_repo(self, project, repo, workspace_root: str) -> None:
        self._project_id = project.id if project is not None else None
        self._repo_id = repo.id if repo is not None else None
        self._reload_videos()

    # -- video list ---------------------------------------------------------

    def _reload_videos(self) -> None:
        self._clear_cards()
        self.player_widget.clear_video()
        # A genuine repo switch (or the tab regaining focus, which also
        # calls set_repo -> _reload_videos) means whatever was selected no
        # longer applies — clearing this here (unlike a plain
        # filter/sort/view-size change, which goes through _apply_filter
        # alone and should keep the current selection) is what makes
        # _restore_or_default_selection fall back to the latest video, per
        # the user's own request that opening the tab always default to
        # the most recent playblast.
        self._selected_video_path = None
        self._video_root = None
        self._all_videos = []
        self._parsed_by_video = {}
        if self._project_id and self._repo_id:
            self._video_root = video_path_store.resolve_video_root(self._api, self._project_id, self._repo_id)
        self._update_empty_state()
        if self._video_root is None or not self._video_root.is_dir():
            self.filter_sidebar.set_available_values({})
            return

        # Recursive: a video flat-named under UkorePlayblast's 2026-07-20
        # naming convention lives directly in video_root, but an older
        # playblast from before that date may still sit nested under its
        # own <sequence>/<shot_code>/vNNN/ subfolder (left alone there per
        # the user's own decision — see UkorePlayblast/README.md) — both
        # need to show up here.
        self._all_videos = [
            p for p in self._video_root.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS
        ]
        for video_path in self._all_videos:
            self._parsed_by_video[video_path] = video_naming.parse_video_filename(video_path)

        self.filter_sidebar.set_available_values(self._collect_filter_values())
        self._apply_filter()

    def _collect_filter_values(self) -> dict:
        """{"sequence": [...], "shot_code": [...], ...} — every distinct
        value currently present across `_all_videos`, for
        `filter_sidebar.set_available_values`. A video that doesn't parse
        under the naming convention contributes "Unknown" to every
        video_naming-derived category instead of being left out. No more
        "commenter" category (dropped 2026-08-08 — comment_store.py, its
        data source, moved to cache/plugins/BananaSketch/ along with the
        rest of the draw/comment editor; this plugin no longer reads
        comment data at all)."""
        values = {field: set() for field in _NAMING_FILTER_FIELDS}
        for video_path in self._all_videos:
            parsed = self._parsed_by_video.get(video_path)
            for field in _NAMING_FILTER_FIELDS:
                values[field].add(_format_filter_value(field, parsed))
        return {key: _sort_with_unknown_last(v) for key, v in values.items()}

    def _clear_cards(self) -> None:
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards = {}
        self._selected_card = None

    def _video_matches_filters(self, video_path: Path) -> bool:
        search = self.filter_sidebar.search_text()
        if search and search not in str(video_path.relative_to(self._video_root)).lower():
            return False
        parsed = self._parsed_by_video.get(video_path)
        for field in _NAMING_FILTER_FIELDS:
            selected = self.filter_sidebar.selected_values(field)
            if not selected:
                continue
            if _format_filter_value(field, parsed) not in selected:
                return False
        return True

    def _sort_videos(self, videos: list[Path]) -> list[Path]:
        if self._sort_mode == _SORT_NAME_ASC:
            return sorted(videos, key=lambda p: str(p.relative_to(self._video_root)).lower())
        if self._sort_mode == _SORT_NAME_DESC:
            return sorted(videos, key=lambda p: str(p.relative_to(self._video_root)).lower(), reverse=True)
        if self._sort_mode == _SORT_OLDEST:
            return sorted(videos, key=lambda p: p.stat().st_mtime)
        return sorted(videos, key=lambda p: p.stat().st_mtime, reverse=True)  # _SORT_NEWEST, the default

    def _set_sort_mode(self, mode: str) -> None:
        self._sort_mode = mode
        self._apply_filter()

    def _set_card_size_mode(self, mode: str) -> None:
        self._card_size_mode = mode
        self._apply_filter()

    def _apply_filter(self) -> None:
        self._clear_cards()
        if self._video_root is None:
            return
        videos = self._sort_videos([p for p in self._all_videos if self._video_matches_filters(p)])
        size = _CARD_SIZES[self._card_size_mode]
        for video_path in videos:
            card = _VideoCard(video_path, video_root=self._video_root, parent=self.cards_container, **size)
            card.clicked.connect(lambda c=card: self._select_card(c))
            self.cards_layout.addWidget(card)
            self._cards[str(video_path)] = card
            self._thumbnail_loader.request(video_path)
        self.list_empty_label.setVisible(not videos)
        self.cards_scroll.setVisible(bool(videos))
        self._restore_or_default_selection(videos)

    def _restore_or_default_selection(self, videos: list[Path]) -> None:
        """Keeps whichever video was already selected across a filter/sort/
        view-size rebuild (_clear_cards always tears down and recreates
        every _VideoCard) if it's still in the current list; otherwise —
        most notably right after _reload_videos' first load for a newly
        opened/refocused repo, which resets _selected_video_path to None —
        falls back to the most recently modified video, so opening the
        UkoreShot tab always shows the latest playblast by default, per
        the user's own request, independent of whatever sort mode happens
        to be active."""
        if not videos:
            self._selected_video_path = None
            return
        target = self._selected_video_path
        if target is None or target not in videos:
            target = max(videos, key=lambda p: p.stat().st_mtime)
        card = self._cards.get(str(target))
        if card is not None:
            self._select_card(card)

    def _on_thumbnail_ready(self, video_path_str: str, pixmap: QPixmap) -> None:
        card = self._cards.get(video_path_str)
        if card is not None:
            card.set_thumbnail(pixmap)

    def _update_empty_state(self) -> None:
        if not self._project_id or not self._repo_id:
            self.empty_label.setText("Select a repo to see this information.")
            show_exclusive(self.empty_label, self.content_widget)
            return
        if self._video_root is None:
            self.empty_label.setText(
                "No video library configured for this repo yet — set one in Repository Setting > UkoreShot."
            )
            show_exclusive(self.empty_label, self.content_widget)
            return
        show_exclusive(self.content_widget, self.empty_label)

    def _select_card(self, card: _VideoCard) -> None:
        if self._selected_card is not None:
            self._selected_card.set_selected(False)
        card.set_selected(True)
        self._selected_card = card
        self._selected_video_path = card.video_path
        self.player_widget.load_video(card.video_path)

    def _on_edit_comment_clicked(self) -> None:
        if self._selected_card is None or self._host is None:
            return
        self._host.navigate_and_focus("banana_sketch", self._selected_card.video_path)

    def _on_send_discord_clicked(self) -> None:
        if self._selected_card is None or self._project_id is None or self._repo_id is None:
            return
        video_path = self._selected_card.video_path
        parsed = self._parsed_by_video.get(video_path)
        if not parsed:
            QMessageBox.warning(
                self,
                "Send to Discord",
                "This video's filename doesn't match the playblast naming convention, so its shot code can't be "
                "determined — Send to Discord needs one to find or create the matching forum post.",
            )
            return
        shot_title = parsed["shot_code"]

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
        if self._selected_video_path is not None:
            self.player_widget.send_discord_button.setEnabled(True)
        QMessageBox.information(self, "Sent to Discord", "Video posted to Discord.")

    def _on_discord_send_failed(self, message: str) -> None:
        self._discord_worker = None
        if self._selected_video_path is not None:
            self.player_widget.send_discord_button.setEnabled(True)
        QMessageBox.warning(self, "Discord Send Failed", message)


def _sort_with_unknown_last(values: set) -> list[str]:
    return sorted(values - {_UNKNOWN}) + ([_UNKNOWN] if _UNKNOWN in values else [])

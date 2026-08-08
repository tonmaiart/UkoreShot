from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.theme import DEFAULT_THEME_NAME, get_theme
from ukoreshot_plugin.core import comment_store
from ukoreshot_plugin.interface.comment_sidebar import CommentSidebar
from ukoreshot_plugin.interface.comment_thread import CommentThread
from ukoreshot_plugin.interface.draw_overlay import (
    TOOL_BRUSH,
    TOOL_ERASER,
    TOOL_SELECT,
    TOOL_TEXT,
    DrawOverlay,
    ReadOnlyCommentOverlay,
    Stroke,
)

_DEFAULT_FPS = 24
# cache/plugins/UkoreShot/interface/player_widget.py, one parent up is
# cache/plugins/UkoreShot/ itself (this plugin's own images/ folder, not
# the shared data/icons/ every other plugin uses — confirmed with the user
# 2026-07-21 that UkoreShot's icons live locally in the plugin instead, see
# images/README.md).
_ICONS_DIR = Path(__file__).resolve().parents[1] / "images"
_BRUSH_ICON_PATH = _ICONS_DIR / "icons8-paint-50.png"
_ERASER_ICON_PATH = _ICONS_DIR / "icons8-eraser-50.png"
_TEXT_ICON_PATH = _ICONS_DIR / "icons8-text-50.png"
_SELECT_ICON_PATH = _ICONS_DIR / "icons8-cursor-24.png"
_CLEAR_ICON_PATH = _ICONS_DIR / "icons8-delete-all-50.png"
_PREV_FRAME_ICON_PATH = _ICONS_DIR / "icons8-chevron-left-26.png"
_NEXT_FRAME_ICON_PATH = _ICONS_DIR / "icons8-right-26.png"
_PREV_COMMENT_ICON_PATH = _ICONS_DIR / "icons8-double-left-26.png"
_NEXT_COMMENT_ICON_PATH = _ICONS_DIR / "icons8-double-right-26.png"
_UNDO_ICON_PATH = _ICONS_DIR / "icons8-undo-30.png"
_REDO_ICON_PATH = _ICONS_DIR / "icons8-redo-30.png"
_PLAY_ICON_PATH = _ICONS_DIR / "icons8-play-50.png"
_PAUSE_ICON_PATH = _ICONS_DIR / "icons8-pause-50.png"
_SHOW_ICON_PATH = _ICONS_DIR / "icons8-show-50.png"
_HIDE_ICON_PATH = _ICONS_DIR / "icons8-hide-50.png"
_EDIT_COMMENT_ICON_PATH = _ICONS_DIR / "icons8-edit-50.png"
# Not added to images/ yet — _set_button_icon falls back to a "Discord" text
# label until a real icon file is placed here, same as every other button's
# pre-icon state (see images/README.md's own note on this fallback).
_DISCORD_ICON_PATH = _ICONS_DIR / "icons8-discord-50.png"
_EDIT_COMMENT_BUTTON_SIZE = 32
_COLOR_BUTTON_SIZE = 24  # "1x1" square swatch, no "Color" text label — per the user's own 2026-08-03 request

_MARK_COLOR = QColor(get_theme(DEFAULT_THEME_NAME).warning)


class _VideoSurface(QWidget):
    """Paints the live video frame itself instead of using QVideoWidget.
    Fixed 2026-07-20 after DrawOverlay's brush still didn't respond to any
    mouse input even with WA_AlwaysStackOnTop set: QVideoWidget renders
    through a real native OS window handle (confirmed via Qt's own docs on
    its platform backends), and native child windows are Z-ordered/
    hit-tested by the OS window manager directly — a Qt-level attribute on
    the *sibling* DrawOverlay can't move that. Reuses the exact
    QVideoSink.videoFrameChanged -> QVideoFrame.toImage() frame-grab
    thumbnail_loader.py already relies on for its one-shot thumbnails,
    just applied to every frame — this widget has no native window at
    all. Stacked with DrawOverlay via _VideoStack (below), not
    QStackedLayout — see that class's docstring for why."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_image: QImage | None = None

    def set_frame(self, image: QImage) -> None:
        self._frame_image = image
        self.update()

    def clear_frame(self) -> None:
        self._frame_image = None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._frame_image is not None and not self._frame_image.isNull():
            scaled = self._frame_image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
        painter.end()


class _FrameNumberOverlay(QWidget):
    """Always-on-top-right HUD showing the current frame number — white
    fill, black stroke, large bold text, per the user's own spec.
    WA_TransparentForMouseEvents so it never intercepts clicks meant for
    DrawOverlay beneath it (the exact class of bug
    developer/bug-history/2026-07-20-draw-overlay-native-video-widget.md and
    2026-07-20-text-tool-drew-strokes-simultaneously.md were about — this
    HUD must never be another thing standing between the mouse and
    drawing). No visibility toggle (removed 2026-07-20 per the user's own
    request — always shown in both modes) — the toggle that does exist
    now is for ReadOnlyCommentOverlay's actual comment content, a
    different thing (see PlayerWidget's toggle_comment_overlay_button).

    Stroke is drawn OUTSIDE the white fill, not straddling it: a
    QPainterPath fill+stroke draw (the first attempt) centers the pen on
    the path outline, so half the stroke width eats into the fill from
    the edges inward, making the glyph look *thinner*, not bolder — the
    opposite of what "stroke ให้มัน stroke outside" asked for. Instead
    this stamps the black text at a ring of offset positions first, then
    paints the white text once, dead center, completely on top — the
    black only ever shows through around the true glyph's outside edge."""

    _MARGIN = 12
    _STROKE_WIDTH = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._text = ""
        self._font = QFont()
        self._font.setPointSize(22)
        self._font.setBold(True)

    def set_frame_index(self, index: int) -> None:
        self._text = str(index)
        self.update()

    def clear(self) -> None:
        self._text = ""
        self.update()

    def paintEvent(self, event) -> None:
        if not self._text:
            return
        metrics = QFontMetrics(self._font)
        x = self.width() - metrics.horizontalAdvance(self._text) - self._MARGIN
        y = self._MARGIN + metrics.ascent()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self._font)
        painter.setPen(Qt.black)
        w = self._STROKE_WIDTH
        for dx in (-w, 0, w):
            for dy in (-w, 0, w):
                if dx or dy:
                    painter.drawText(x + dx, y + dy, self._text)
        painter.setPen(Qt.white)
        painter.drawText(x, y, self._text)
        painter.end()


class _CommentAwareSlider(QSlider):
    """QSlider with small tick marks overlaid at specific millisecond
    positions (frames with a saved comment), so scrubbing shows at a
    glance roughly where feedback exists along the timeline — "พออนุมานได้"
    (good enough to infer from), the user's own bar, not a pixel-perfect
    match against the native groove rect for every Qt style."""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._mark_positions_ms: list[int] = []

    def set_marks(self, positions_ms: list[int]) -> None:
        self._mark_positions_ms = positions_ms
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        span = self.maximum() - self.minimum()
        if not self._mark_positions_ms or span <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(_MARK_COLOR)
        usable_width = max(1, self.width() - 8)
        for ms in self._mark_positions_ms:
            fraction = max(0.0, min(1.0, (ms - self.minimum()) / span))
            x = 4 + fraction * usable_width
            painter.drawLine(int(x), self.height() - 5, int(x), self.height())
        painter.end()


class _VideoStack(QWidget):
    """Manually layers `base` (the video surface), an optional `overlay`
    (DrawOverlay in edit mode, ReadOnlyCommentOverlay in view mode), and
    an optional `hud` (_FrameNumberOverlay) on top of each other, all
    filling this widget's whole area. Replaces `QStackedLayout(StackAll)`
    2026-07-20 after `core.extensibility.debug_log` evidence (see
    plugins/core/DebugConsole/) showed DrawOverlay ending up with
    `isVisible() == False` and never receiving a single mouse event
    despite correct, non-zero geometry — `QStackedLayout`'s actual runtime
    behavior here didn't match what its `StackAll` docs promise (or some
    other layout subtlety was silently defeating it). Explicit
    `setParent`/`show()`/`setGeometry`/`raise_()` removes every bit of
    that ambiguity: every widget is a plain sibling, all are
    unconditionally shown, and `raise_()` on every resize guarantees
    `overlay` then `hud` stay on top in that order for both painting AND
    mouse hit-testing — the standard, well-documented way Qt resolves
    z-order among sibling widgets, unlike a layout's internal stacking
    mode. `hud` is raised last (i.e. topmost) but is itself
    WA_TransparentForMouseEvents, so it never actually intercepts
    anything meant for `overlay`."""

    def __init__(self, base: QWidget, overlay: QWidget | None, hud: QWidget | None = None, parent=None):
        super().__init__(parent)
        self._base = base
        self._overlay = overlay
        self._hud = hud
        base.setParent(self)
        base.show()
        if overlay is not None:
            overlay.setParent(self)
            overlay.show()
            overlay.raise_()
        if hud is not None:
            hud.setParent(self)
            hud.show()
            hud.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._base.setGeometry(0, 0, self.width(), self.height())
        if self._overlay is not None:
            self._overlay.setGeometry(0, 0, self.width(), self.height())
            self._overlay.raise_()
        if self._hud is not None:
            self._hud.setGeometry(0, 0, self.width(), self.height())
            self._hud.raise_()


class PlayerWidget(QWidget):
    """Video playback + frame-by-frame grease-pencil drawing/comment
    review, for whichever video UkoreShotPage's list has selected. Frame
    stepping/indexing is an FPS-based approximation
    (round(position_ms / 1000 * fps)) — QMediaPlayer has no native
    frame-accurate API for arbitrary containers; confirmed with the user as
    an acceptable simplification ("แบบโง่") rather than something to solve
    precisely. `_fps_value` (fixed at `_DEFAULT_FPS`, shown read-only via
    `fps_label`) drives it — no editable FPS control (removed 2026-07-20
    per the user's own request: "we're not going to change the frame rate
    value anyway").

    `show_edit_tools=False` (used for the library page's inline preview)
    skips building the interactive draw toolbar/comment thread entirely,
    but still shows a read-only rendering of each frame's saved
    strokes/text boxes (`ReadOnlyCommentOverlay`, toggleable via
    `toggle_comment_overlay_button`) plus the same `comment_sidebar` and
    prev/next-commented-frame controls edit mode has, plus its own
    `edit_comment_button` (emits `editCommentRequested`, connected by
    `video_library_page.py` to actually open `EditVideoDialog`).
    `EditVideoDialog` uses the default `True` to get the full interactive
    annotation experience."""

    editCommentRequested = Signal()
    sendToDiscordRequested = Signal()

    def __init__(self, parent=None, *, show_edit_tools: bool = True):
        super().__init__(parent)
        self._show_edit_tools = show_edit_tools
        self._video_path: Path | None = None
        self._frames: dict = {"frames": {}}
        self._current_frame_index = 0
        self._scrubbing = False
        self._fps_value = _DEFAULT_FPS

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.video_sink = QVideoSink(self)
        self.player.setVideoSink(self.video_sink)
        self.video_sink.videoFrameChanged.connect(self._on_video_frame)
        self.video_surface = _VideoSurface()
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)

        if show_edit_tools:
            self.draw_overlay = DrawOverlay()
            self.draw_overlay.strokesChanged.connect(self._on_strokes_changed)
            self.draw_overlay.textBoxesChanged.connect(self._on_text_boxes_changed)
            self.draw_overlay.toolSizeChanged.connect(self._on_tool_size_changed)
        else:
            self.readonly_comment_overlay = ReadOnlyCommentOverlay()
        self.frame_number_overlay = _FrameNumberOverlay()
        video_stack_widget = _VideoStack(
            self.video_surface,
            self.draw_overlay if show_edit_tools else self.readonly_comment_overlay,
            self.frame_number_overlay,
        )

        if show_edit_tools:
            # Undo/Redo — leftmost in the row (moved there 2026-08-03 per
            # the user's own request; used to sit at the end of the old
            # separate draw_row). self.draw_overlay already exists (built
            # earlier in this constructor) so these wire straight to it.
            self.undo_button = QPushButton()
            self._set_button_icon(self.undo_button, _UNDO_ICON_PATH, "Undo")
            self.undo_button.clicked.connect(self.draw_overlay.undo)
            self.redo_button = QPushButton()
            self._set_button_icon(self.redo_button, _REDO_ICON_PATH, "Redo")
            self.redo_button.clicked.connect(self.draw_overlay.redo)

            # Brush/Eraser/Text/Select — kept as their own cluster in this
            # row (moved here 2026-07-20 per the user's own request; Select
            # added 2026-08-03, also per the user's own request, purely for
            # moving/deleting an existing stroke or text box — see
            # draw_overlay.py's DrawOverlay/TOOL_SELECT docstring). Four
            # mutually exclusive modes (QButtonGroup) — not "three modes
            # plus a one-shot action": Text used to be a plain click action
            # that left Brush's drawing active underneath, so repositioning
            # a text box could also draw a stroke at the same time (fixed
            # 2026-07-20 — see draw_overlay.py's DrawOverlay._tool). Icons
            # expected at ../images/icons8-{paint,eraser,text,cursor}-*.png
            # — _set_button_icon falls back to a text label instead of a
            # blank button when a given icon file isn't there yet.
            # `checked` styling (core/theme.py's QToolButton:checked) is
            # what actually makes the active tool visually obvious, not
            # the icon/text itself.
            self.brush_tool_button = QToolButton()
            self._set_button_icon(self.brush_tool_button, _BRUSH_ICON_PATH, "Brush")
            self.brush_tool_button.setCheckable(True)
            self.brush_tool_button.setChecked(True)
            self.brush_tool_button.setToolTip("Brush (1)")
            self.brush_tool_button.toggled.connect(
                lambda checked: self.draw_overlay.set_tool(TOOL_BRUSH) if checked else None
            )
            self.eraser_tool_button = QToolButton()
            self._set_button_icon(self.eraser_tool_button, _ERASER_ICON_PATH, "Eraser")
            self.eraser_tool_button.setCheckable(True)
            self.eraser_tool_button.setToolTip("Eraser (2)")
            self.eraser_tool_button.toggled.connect(
                lambda checked: self.draw_overlay.set_tool(TOOL_ERASER) if checked else None
            )
            self.text_tool_button = QToolButton()
            self._set_button_icon(self.text_tool_button, _TEXT_ICON_PATH, "Text")
            self.text_tool_button.setCheckable(True)
            self.text_tool_button.setToolTip("Text (3) — click empty canvas to add, click existing text to edit")
            self.text_tool_button.toggled.connect(
                lambda checked: self.draw_overlay.set_tool(TOOL_TEXT) if checked else None
            )
            self.select_tool_button = QToolButton()
            self._set_button_icon(self.select_tool_button, _SELECT_ICON_PATH, "Select")
            self.select_tool_button.setCheckable(True)
            self.select_tool_button.setToolTip("Select (4) — move or Delete an existing stroke/text box")
            self.select_tool_button.toggled.connect(
                lambda checked: self.draw_overlay.set_tool(TOOL_SELECT) if checked else None
            )
            self._tool_button_group = QButtonGroup(self)
            self._tool_button_group.setExclusive(True)
            self._tool_button_group.addButton(self.brush_tool_button)
            self._tool_button_group.addButton(self.eraser_tool_button)
            self._tool_button_group.addButton(self.text_tool_button)
            self._tool_button_group.addButton(self.select_tool_button)

            # Color — a plain 1x1 square swatch, no "Color" text label
            # (changed 2026-08-03 per the user's own request); the
            # background-color itself (see _update_color_button) is
            # already the whole point of the button.
            self.color_button = QPushButton()
            self.color_button.setFixedSize(_COLOR_BUTTON_SIZE, _COLOR_BUTTON_SIZE)
            self.color_button.setToolTip("Brush color")
            self.color_button.clicked.connect(self._on_pick_color)
            self._current_color = QColor("#ff3b30")
            self._update_color_button()
            # Shared by both Brush (stroke width) and Eraser (hit-test
            # radius) — see draw_overlay.py's _brush_width docstring. Also
            # adjustable via the "F" resize gesture on the canvas itself
            # (DrawOverlay.toolSizeChanged keeps this slider in sync then).
            self.brush_slider = QSlider(Qt.Horizontal)
            self.brush_slider.setRange(1, 60)
            self.brush_slider.setValue(4)
            self.brush_slider.setMaximumWidth(120)
            self.brush_slider.valueChanged.connect(self.draw_overlay.set_brush_width)

            # Clear Frame — flush right (changed 2026-08-03 per the user's
            # own request), an icon button instead of a text button.
            self.clear_button = QPushButton()
            self._set_button_icon(self.clear_button, _CLEAR_ICON_PATH, "Clear Frame")
            self.clear_button.setToolTip("Clear Frame")
            self.clear_button.clicked.connect(self.draw_overlay.clear_frame)

            tool_row = QHBoxLayout()
            tool_row.addWidget(self.undo_button)
            tool_row.addWidget(self.redo_button)
            tool_row.addWidget(self.brush_tool_button)
            tool_row.addWidget(self.eraser_tool_button)
            tool_row.addWidget(self.text_tool_button)
            tool_row.addWidget(self.select_tool_button)
            tool_row.addWidget(self.color_button)
            tool_row.addWidget(QLabel("Size"))
            tool_row.addWidget(self.brush_slider)
            tool_row.addStretch()
            tool_row.addWidget(self.clear_button)

        self.play_button = QPushButton()
        self._set_button_icon(self.play_button, _PLAY_ICON_PATH, "Play")
        self.play_button.clicked.connect(self._on_play_clicked)
        self.prev_frame_button = QPushButton()
        self._set_button_icon(self.prev_frame_button, _PREV_FRAME_ICON_PATH, "<")
        self.prev_frame_button.clicked.connect(lambda: self._step_frame(-1))
        self.next_frame_button = QPushButton()
        self._set_button_icon(self.next_frame_button, _NEXT_FRAME_ICON_PATH, ">")
        self.next_frame_button.clicked.connect(lambda: self._step_frame(1))

        # Jumps to the nearest frame (before/after current) that has any
        # strokes/comments/text boxes saved — lets a reviewer step through
        # only the frames someone left feedback on. Available in both
        # modes since 2026-07-20 (was edit-mode-only before).
        self.prev_comment_button = QPushButton()
        self._set_button_icon(self.prev_comment_button, _PREV_COMMENT_ICON_PATH, "◀ Comment")
        self.prev_comment_button.clicked.connect(lambda: self._jump_to_comment_frame(-1))
        self.next_comment_button = QPushButton()
        self._set_button_icon(self.next_comment_button, _NEXT_COMMENT_ICON_PATH, "Comment ▶")
        self.next_comment_button.clicked.connect(lambda: self._jump_to_comment_frame(1))

        # Type-a-frame-number-and-jump — "ช่องกรอกเลข" the user asked for
        # alongside the button reorder. editingFinished (Enter/focus-loss),
        # not valueChanged, so it doesn't jump mid-typing on a 2+ digit
        # number. Range synced to the clip length in _on_duration_changed.
        self.goto_frame_spin = QSpinBox()
        self.goto_frame_spin.setRange(0, 0)
        self.goto_frame_spin.setToolTip("Go to frame")
        self.goto_frame_spin.editingFinished.connect(self._on_goto_frame_entered)

        # Read-only info, not editable — removed 2026-07-20 per the user's
        # own request ("we're not going to change the frame rate value
        # anyway"), was a QSpinBox before.
        self.fps_label = QLabel(f"{self._fps_value} FPS")
        self.fps_label.setProperty("secondary", True)

        # 0.01x-1.00x — a slow-motion control, deliberately capped at normal
        # speed rather than a general-purpose speed dial.
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(1, 100)
        self.speed_slider.setValue(100)
        self.speed_slider.setMaximumWidth(100)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        self.speed_label = QLabel("1.00x")
        self.speed_label.setMinimumWidth(40)

        # Own row, separate from transport_row (below), per the user's own
        # 2026-07-20 request — leftmost/rightmost labels show the frame
        # range, and the slider itself gets comment tick marks (see
        # _CommentAwareSlider, _refresh_comment_indicators).
        self.start_frame_label = QLabel("0")
        self.end_frame_label = QLabel("0")
        self.position_slider = _CommentAwareSlider(Qt.Horizontal)
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)

        slider_row = QHBoxLayout()
        slider_row.addWidget(self.start_frame_label)
        slider_row.addWidget(self.position_slider, stretch=1)
        slider_row.addWidget(self.end_frame_label)

        # Button order — "previous comment, previous, play/stop, next, next
        # comment" per the user's own 2026-07-20 request.
        transport_row = QHBoxLayout()
        transport_row.addWidget(self.prev_comment_button)
        transport_row.addWidget(self.prev_frame_button)
        transport_row.addWidget(self.play_button)
        transport_row.addWidget(self.next_frame_button)
        transport_row.addWidget(self.next_comment_button)
        transport_row.addWidget(self.goto_frame_spin)
        transport_row.addWidget(self.fps_label)
        transport_row.addStretch()
        transport_row.addWidget(QLabel("Speed"))
        transport_row.addWidget(self.speed_slider)
        transport_row.addWidget(self.speed_label)

        if not show_edit_tools:
            # View mode only. toggle_comment_overlay_button: icon swaps
            # show/hide per its checked state (icons8-hide-50.png when
            # comments ARE visible — click to hide — icons8-show-50.png
            # otherwise), toggles ReadOnlyCommentOverlay's visibility (the
            # actual drawn strokes/text boxes, read-only) — added
            # 2026-07-20 so the plain viewing page can see comment content
            # without opening the full Edit Video dialog. Not the same
            # thing as the old frame-number toggle (removed the same day
            # — frame_number_overlay now has no toggle at all, always
            # shown). edit_comment_button: a fixed 1:1 square icon button,
            # last in the row (floats at the bottom-right corner),
            # emitting editCommentRequested for video_library_page.py to
            # actually open EditVideoDialog — moved here from that page
            # 2026-07-20 so it sits directly next to Show/Hide Comments
            # rather than in its own separate row below the whole player.
            self.toggle_comment_overlay_button = QPushButton()
            self.toggle_comment_overlay_button.setCheckable(True)
            self.toggle_comment_overlay_button.setChecked(True)
            self._update_comment_overlay_toggle_icon(True)
            self.toggle_comment_overlay_button.toggled.connect(self._on_toggle_comment_overlay)
            self.edit_comment_button = QPushButton()
            self._set_button_icon(self.edit_comment_button, _EDIT_COMMENT_ICON_PATH, "Edit")
            self.edit_comment_button.setFixedSize(_EDIT_COMMENT_BUTTON_SIZE, _EDIT_COMMENT_BUTTON_SIZE)
            self.edit_comment_button.setEnabled(False)
            self.edit_comment_button.clicked.connect(self.editCommentRequested.emit)
            # send_discord_button: posts whichever video is loaded to the
            # repo's configured Discord channel — video_library_page.py
            # connects sendToDiscordRequested and owns the actual send
            # (reading the channel/token, running DiscordSendWorker), same
            # division of labor as edit_comment_button/editCommentRequested.
            self.send_discord_button = QPushButton()
            self._set_button_icon(self.send_discord_button, _DISCORD_ICON_PATH, "Discord")
            self.send_discord_button.setToolTip("Send to Discord")
            self.send_discord_button.setEnabled(False)
            self.send_discord_button.clicked.connect(self.sendToDiscordRequested.emit)
            transport_row.addWidget(self.toggle_comment_overlay_button)
            transport_row.addWidget(self.edit_comment_button)
            transport_row.addWidget(self.send_discord_button)

        # "A"/"D" step one frame back/forward, "Space" toggles play/pause,
        # "Shift+A"/"Shift+D" jump to the previous/next commented frame
        # (both modes since 2026-07-20 — were edit-mode-only before) — all
        # skipped while a text field has focus (comment_thread.input_edit,
        # goto_frame_spin's internal line edit, or a text box) via
        # _is_typing() so typing those letters/space/digits doesn't
        # hijack the cursor instead.
        self._prev_frame_shortcut = QShortcut(QKeySequence(Qt.Key_A), self)
        self._prev_frame_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._prev_frame_shortcut.activated.connect(lambda: self._step_frame_if_not_typing(-1))
        self._next_frame_shortcut = QShortcut(QKeySequence(Qt.Key_D), self)
        self._next_frame_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._next_frame_shortcut.activated.connect(lambda: self._step_frame_if_not_typing(1))
        self._play_pause_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._play_pause_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._play_pause_shortcut.activated.connect(self._toggle_play_if_not_typing)
        self._prev_comment_shortcut = QShortcut(QKeySequence("Shift+A"), self)
        self._prev_comment_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._prev_comment_shortcut.activated.connect(lambda: self._jump_to_comment_frame_if_not_typing(-1))
        self._next_comment_shortcut = QShortcut(QKeySequence("Shift+D"), self)
        self._next_comment_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._next_comment_shortcut.activated.connect(lambda: self._jump_to_comment_frame_if_not_typing(1))
        if show_edit_tools:
            # "Ctrl+Z"/"Ctrl+Shift+Z" for Undo/Redo — same _is_typing()
            # guard as everything else here, which matters more for these
            # two than most: QTextEdit/QPlainTextEdit already have their
            # own native Ctrl+Z, so typing in comment_thread's input or a
            # text box must not also fire the drawing undo.
            self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
            self._undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._undo_shortcut.activated.connect(self._undo_if_not_typing)
            self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
            self._redo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._redo_shortcut.activated.connect(self._redo_if_not_typing)
            # "1"/"2"/"3"/"4" select Brush/Eraser/Text/Select — same
            # _is_typing() guard.
            self._brush_tool_shortcut = QShortcut(QKeySequence(Qt.Key_1), self)
            self._brush_tool_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._brush_tool_shortcut.activated.connect(lambda: self._select_tool_if_not_typing(self.brush_tool_button))
            self._eraser_tool_shortcut = QShortcut(QKeySequence(Qt.Key_2), self)
            self._eraser_tool_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._eraser_tool_shortcut.activated.connect(lambda: self._select_tool_if_not_typing(self.eraser_tool_button))
            self._text_tool_shortcut = QShortcut(QKeySequence(Qt.Key_3), self)
            self._text_tool_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._text_tool_shortcut.activated.connect(lambda: self._select_tool_if_not_typing(self.text_tool_button))
            self._select_tool_shortcut = QShortcut(QKeySequence(Qt.Key_4), self)
            self._select_tool_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._select_tool_shortcut.activated.connect(lambda: self._select_tool_if_not_typing(self.select_tool_button))

        # main_column holds everything this widget used to lay out
        # directly on itself — now wrapped so comment_sidebar (below) can
        # sit to its right in an outer QHBoxLayout(self).
        main_column = QWidget()
        layout = QVBoxLayout(main_column)
        layout.setContentsMargins(0, 0, 0, 0)
        if show_edit_tools:
            layout.addLayout(tool_row)
        layout.addWidget(video_stack_widget, stretch=1)
        layout.addLayout(slider_row)
        layout.addLayout(transport_row)

        if show_edit_tools:
            # Facebook-style multi-user comment thread — replaces the old
            # single note_edit QTextEdit 2026-07-20 per the user's own
            # request ("เหมือนกด comment fb"): any number of comments per
            # frame, each tagged with an author + timestamp, each
            # individually deletable. See comment_thread.py. Lives in its
            # own thread_column (below), not main_column, so it gets a
            # full-height column to itself at the far right — split out
            # from right_column (which still just holds comment_sidebar)
            # 2026-08-03 per the user's own request for more comment-box
            # height and its own rightmost column.
            self.comment_thread = CommentThread()
            self.comment_thread.commentsChanged.connect(self._on_comments_changed)

            self.draw_overlay.set_color(self._current_color)

        # Right-hand column — comment_sidebar (frame list, both modes), per
        # the user's own 2026-07-20 request to move the comment box to the
        # right side. Used in both modes (per an earlier 2026-07-20
        # request: view mode gets "the same" sidebar edit mode has).
        # Populated from self._frames whenever that data changes — see
        # load_video/clear_video/_save_current_frame, all of which call
        # _refresh_comment_indicators().
        self.comment_sidebar = CommentSidebar()
        self.comment_sidebar.frameSelected.connect(self._jump_to_frame)

        right_column = QWidget()
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.comment_sidebar, stretch=1)

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(main_column, stretch=1)
        outer_layout.addWidget(right_column)

        if show_edit_tools:
            # comment_thread gets its own full-height column at the far
            # right (split out from right_column 2026-08-03 per the user's
            # own request for more comment-box height) — stretch=1 here
            # plus comment_thread.py's own thread_scroll stretch lets it
            # fill the column instead of being capped to a short strip
            # underneath comment_sidebar.
            thread_column = QWidget()
            thread_layout = QVBoxLayout(thread_column)
            thread_layout.setContentsMargins(0, 0, 0, 0)
            thread_layout.addWidget(self.comment_thread, stretch=1)
            outer_layout.addWidget(thread_column)

        self._set_paused_state(True)

    # -- loading --------------------------------------------------------

    def load_video(self, video_path: Path) -> None:
        self._video_path = video_path
        # Loaded regardless of show_edit_tools now — comment_sidebar needs
        # to know which frames have comments even in the read-only inline
        # preview (view mode gained its own comment sidebar 2026-07-20).
        self._frames = comment_store.load(video_path)
        self._refresh_comment_indicators()
        self.player.setSource(QUrl.fromLocalFile(str(video_path)))
        self.player.pause()
        self._current_frame_index = 0
        self._load_current_frame()
        if not self._show_edit_tools:
            self.edit_comment_button.setEnabled(True)
            self.send_discord_button.setEnabled(True)

    def clear_video(self) -> None:
        self._video_path = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.video_surface.clear_frame()
        self.frame_number_overlay.clear()
        self._frames = {"frames": {}}
        self._refresh_comment_indicators()
        if self._show_edit_tools:
            self.draw_overlay.load_frame([], [])
            self.comment_thread.set_comments([])
        else:
            self.readonly_comment_overlay.set_frame([], [])
            self.edit_comment_button.setEnabled(False)
            self.send_discord_button.setEnabled(False)

    def _on_video_frame(self, frame) -> None:
        if not frame.isValid():
            return
        image = frame.toImage()
        if not image.isNull():
            self.video_surface.set_frame(image)

    # -- transport --------------------------------------------------------

    def _fps(self) -> int:
        return self._fps_value

    def _on_play_clicked(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self._set_paused_state(False)
            self.player.play()

    def _set_paused_state(self, paused: bool) -> None:
        self._set_button_icon(self.play_button, _PLAY_ICON_PATH if paused else _PAUSE_ICON_PATH, "Play" if paused else "Pause")
        if self._show_edit_tools:
            self.draw_overlay.set_drawing_enabled(paused)

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.position_slider.setRange(0, max(0, duration_ms))
        total_frames = round(duration_ms / 1000 * self._fps())
        self.end_frame_label.setText(str(total_frames))
        self.goto_frame_spin.setRange(0, max(0, total_frames))

    def _on_position_changed(self, position_ms: int) -> None:
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self._set_paused_state(True)
        if not self._scrubbing:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position_ms)
            self.position_slider.blockSignals(False)
        frame_index = round(position_ms / 1000 * self._fps())
        if frame_index != self._current_frame_index:
            self._current_frame_index = frame_index
            self.frame_number_overlay.set_frame_index(frame_index)
            self.comment_sidebar.set_current_frame(frame_index)
            self.goto_frame_spin.blockSignals(True)
            self.goto_frame_spin.setValue(frame_index)
            self.goto_frame_spin.blockSignals(False)
            self._load_current_frame()

    def _on_goto_frame_entered(self) -> None:
        self._jump_to_frame(self.goto_frame_spin.value())

    def _jump_to_frame(self, frame_index: int) -> None:
        """comment_sidebar.frameSelected or goto_frame_spin — land on that
        exact frame (unlike _jump_to_comment_frame, which finds the
        nearest one relative to wherever playback currently is)."""
        self.player.pause()
        self.player.setPosition(round(frame_index / self._fps() * 1000))

    def _refresh_comment_indicators(self) -> None:
        frames = self._frames.get("frames", {})
        self.comment_sidebar.set_frames(frames)
        indices = self._frame_indices_with_comments()
        self.position_slider.set_marks([round(i / self._fps() * 1000) for i in indices])

    def _on_slider_pressed(self) -> None:
        self._scrubbing = True

    def _on_slider_released(self) -> None:
        self._scrubbing = False
        self.player.setPosition(self.position_slider.value())
        self.player.pause()

    def _on_slider_moved(self, value: int) -> None:
        self.player.setPosition(value)

    def _on_speed_changed(self, value: int) -> None:
        rate = value / 100.0
        self.player.setPlaybackRate(rate)
        self.speed_label.setText(f"{rate:.2f}x")

    def _step_frame(self, delta: int) -> None:
        self.player.pause()
        frame_ms = 1000 / self._fps()
        new_position = max(0, self.player.position() + delta * frame_ms)
        self.player.setPosition(round(new_position))

    def _is_typing(self) -> bool:
        """True while a text-input widget (comment_thread.input_edit,
        goto_frame_spin's internal line edit, a text box's
        QPlainTextEdit) has focus — shared by every keyboard shortcut here
        so typing a letter/space/digit that happens to match one doesn't
        hijack the cursor into stepping frames, jumping to a comment, or
        toggling play instead."""
        focus_widget = QApplication.focusWidget()
        return isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit))

    def _step_frame_if_not_typing(self, delta: int) -> None:
        if not self._is_typing():
            self._step_frame(delta)

    def _toggle_play_if_not_typing(self) -> None:
        if not self._is_typing():
            self._on_play_clicked()

    def _select_tool_if_not_typing(self, button: QToolButton) -> None:
        """"1"/"2"/"3" -> Brush/Eraser/Text. Checking the button (rather
        than calling draw_overlay.set_tool directly) keeps the toolbox's
        own checked-state display in sync automatically, same as clicking
        it with the mouse would."""
        if not self._is_typing():
            button.setChecked(True)

    def _frame_indices_with_comments(self) -> list[int]:
        frames = self._frames.get("frames", {})
        return sorted(int(key) for key, entry in frames.items() if entry)

    def _jump_to_comment_frame(self, direction: int) -> None:
        indices = self._frame_indices_with_comments()
        if direction > 0:
            candidates = [i for i in indices if i > self._current_frame_index]
            target = min(candidates, default=None)
        else:
            candidates = [i for i in indices if i < self._current_frame_index]
            target = max(candidates, default=None)
        if target is None:
            return
        self.player.pause()
        self.player.setPosition(round(target / self._fps() * 1000))

    def _jump_to_comment_frame_if_not_typing(self, direction: int) -> None:
        if not self._is_typing():
            self._jump_to_comment_frame(direction)

    def _undo_if_not_typing(self) -> None:
        if not self._is_typing():
            self.draw_overlay.undo()

    def _redo_if_not_typing(self) -> None:
        if not self._is_typing():
            self.draw_overlay.redo()

    def _on_tool_size_changed(self, width: int) -> None:
        """DrawOverlay.toolSizeChanged — fires only from the "F" resize
        gesture (see draw_overlay.py), keeps the Size slider's displayed
        value in sync without re-triggering set_brush_width in a loop."""
        self.brush_slider.blockSignals(True)
        self.brush_slider.setValue(width)
        self.brush_slider.blockSignals(False)

    def _on_toggle_comment_overlay(self, checked: bool) -> None:
        self.readonly_comment_overlay.setVisible(checked)
        self._update_comment_overlay_toggle_icon(checked)

    def _update_comment_overlay_toggle_icon(self, checked: bool) -> None:
        # checked == comments ARE currently shown -> button offers to
        # Hide; unchecked -> button offers to Show.
        self._set_button_icon(
            self.toggle_comment_overlay_button,
            _HIDE_ICON_PATH if checked else _SHOW_ICON_PATH,
            "Hide" if checked else "Show",
        )

    # -- per-frame drawing/comment persistence ---------------------------

    def _load_current_frame(self) -> None:
        frame_entry = self._frames.get("frames", {}).get(str(self._current_frame_index), {})
        strokes = [Stroke.from_dict(s) for s in frame_entry.get("strokes", [])]
        if self._show_edit_tools:
            self.draw_overlay.load_frame(strokes, frame_entry.get("text_boxes", []))
            self.comment_thread.set_comments(self._migrate_comments(frame_entry))
        else:
            self.readonly_comment_overlay.set_frame(strokes, frame_entry.get("text_boxes", []))

    @staticmethod
    def _migrate_comments(frame_entry: dict) -> list[dict]:
        """A frame's "comments" list if present; otherwise wraps a legacy
        single "note" string (saved before 2026-07-20's Facebook-style
        multi-comment thread existed) into a one-item list so old data
        still displays. Saving again replaces "note" with "comments"
        entirely — see _save_current_frame."""
        comments = frame_entry.get("comments")
        if comments:
            return comments
        legacy_note = frame_entry.get("note")
        if legacy_note:
            return [{"id": "legacy", "author": "", "text": legacy_note, "timestamp": ""}]
        return []

    def _on_strokes_changed(self) -> None:
        self._save_current_frame(strokes=self.draw_overlay.current_strokes())

    def _on_text_boxes_changed(self) -> None:
        self._save_current_frame(text_boxes=self.draw_overlay.current_text_boxes())

    def _on_comments_changed(self) -> None:
        self._save_current_frame(comments=self.comment_thread.current_comments())

    def _save_current_frame(
        self,
        *,
        strokes: list[Stroke] | None = None,
        comments: list[dict] | None = None,
        text_boxes: list[dict] | None = None,
    ) -> None:
        if self._video_path is None:
            return
        frames = self._frames.setdefault("frames", {})
        key = str(self._current_frame_index)
        entry = dict(frames.get(key, {}))
        if strokes is not None:
            if strokes:
                entry["strokes"] = [s.to_dict() for s in strokes]
            else:
                entry.pop("strokes", None)
        if comments is not None:
            if comments:
                entry["comments"] = comments
            else:
                entry.pop("comments", None)
            entry.pop("note", None)  # fully migrated off the legacy field once saved again
        if text_boxes is not None:
            if text_boxes:
                entry["text_boxes"] = text_boxes
            else:
                entry.pop("text_boxes", None)
        if entry:
            frames[key] = entry
        else:
            frames.pop(key, None)
        comment_store.save(self._video_path, frames)
        self._refresh_comment_indicators()

    # -- toolbar ----------------------------------------------------------

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(self._current_color, self, "Pen Color")
        if color.isValid():
            self._current_color = color
            self.draw_overlay.set_color(color)
            self._update_color_button()

    def _update_color_button(self) -> None:
        self.color_button.setStyleSheet(f"background-color: {self._current_color.name()};")

    @staticmethod
    def _set_button_icon(button, icon_path: Path, fallback_text: str) -> None:
        """Icon-only when icon_path exists on disk, text-only otherwise —
        so a button never renders as a blank, unclickable-looking square
        before the real data/icons/icons8-*.png file is placed. Works for
        both QToolButton (the Brush/Eraser/Text toolbox) and QPushButton
        (every other icon button here)."""
        if icon_path.is_file():
            button.setIcon(QIcon(str(icon_path)))
            button.setText("")
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            button.setIcon(QIcon())
            button.setText(fallback_text)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonTextOnly)

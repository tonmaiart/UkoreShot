from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication,
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

from ukoreshot_plugin.interface.draw_overlay import DrawOverlay, Stroke, paint_stroke_points
from ukoreshot_plugin.interface.sequence_player import SequencePlayer

_logger = logging.getLogger("UkoreShot.Player")

_DEFAULT_FPS = 24
# cache/plugins/UkoreShot/interface/player_widget.py, one parent up is
# cache/plugins/UkoreShot/ itself (this plugin's own images/ folder, not
# the shared data/icons/ every other plugin uses — confirmed with the user
# 2026-07-21 that UkoreShot's icons live locally in the plugin instead).
_ICONS_DIR = Path(__file__).resolve().parents[1] / "images"
_PREV_FRAME_ICON_PATH = _ICONS_DIR / "icons8-chevron-left-26.png"
_NEXT_FRAME_ICON_PATH = _ICONS_DIR / "icons8-right-26.png"
_PLAY_ICON_PATH = _ICONS_DIR / "icons8-play-50.png"
_PAUSE_ICON_PATH = _ICONS_DIR / "icons8-pause-50.png"


class _VideoSurface(QWidget):
    """Paints the live video frame itself instead of using QVideoWidget.
    Fixed 2026-07-20 after DrawOverlay's brush still didn't respond to any
    mouse input even with WA_AlwaysStackOnTop set: QVideoWidget renders
    through a real native OS window handle (confirmed via Qt's own docs on
    its platform backends), and native child windows are Z-ordered/
    hit-tested by the OS window manager directly — a Qt-level attribute on
    a sibling widget can't move that. Reuses the exact
    QVideoSink.videoFrameChanged -> QVideoFrame.toImage() frame-grab
    thumbnail_loader.py already relies on for its one-shot thumbnails,
    just applied to every frame — this widget has no native window at
    all. Kept even though this plugin no longer has a drawing overlay to
    protect (moved to cache/plugins/BananaSketch/ 2026-08-08) — still the
    right way to paint video frames manually, and still stacked with
    _FrameNumberOverlay via _VideoStack below, not QStackedLayout — see
    that class's docstring for why."""

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


_FRAME_NUMBER_MARGIN = 12
_FRAME_NUMBER_STROKE_WIDTH = 5
_FRAME_NUMBER_POINT_SIZE = 22


def paint_frame_number(painter: QPainter, text: str, width: int) -> None:
    """Module-level, not a _FrameNumberOverlay method — draws `text`
    horizontally centered, top-anchored, white fill with a black stroke
    drawn OUTSIDE the fill (see _FrameNumberOverlay's own docstring for
    why: stamping the black text at a ring of offset positions first, then
    the white text once dead center on top, rather than a single
    QPainterPath fill+stroke draw, which would eat into the fill from the
    edges inward instead of surrounding it). Shared with
    video_library_page.py's Get Video/Get Video - Commented exports
    (2026-08-21) so a burned-in frame number in an exported video matches
    this live HUD's own look exactly — same pattern draw_overlay.py's
    paint_stroke_points already uses for stroke rendering. Assumes
    painter's font/pen aren't needed for anything else by the caller
    afterward (both set here, not restored)."""
    if not text:
        return
    font = QFont()
    font.setPointSize(_FRAME_NUMBER_POINT_SIZE)
    font.setBold(True)
    metrics = QFontMetrics(font)
    x = (width - metrics.horizontalAdvance(text)) // 2
    y = _FRAME_NUMBER_MARGIN + metrics.ascent()
    painter.setFont(font)
    painter.setPen(Qt.black)
    w = _FRAME_NUMBER_STROKE_WIDTH
    for dx in (-w, 0, w):
        for dy in (-w, 0, w):
            if dx or dy:
                painter.drawText(x + dx, y + dy, text)
    painter.setPen(Qt.white)
    painter.drawText(x, y, text)


class _FrameNumberOverlay(QWidget):
    """Always-on-top HUD showing the current frame number — white fill,
    black stroke, large bold text, per the user's own spec. Horizontally
    centered (changed 2026-08-21, was right-aligned), still top-anchored.
    WA_TransparentForMouseEvents so it never intercepts clicks meant for
    anything beneath it (the exact class of bug
    developer/bug-history/2026-07-20-draw-overlay-native-video-widget.md and
    2026-07-20-text-tool-drew-strokes-simultaneously.md were about, back
    when this plugin still had its own drawing overlay — moved to
    cache/plugins/BananaSketch/ 2026-08-08). No visibility toggle — always
    shown. Painting itself delegates to the module-level paint_frame_number
    above."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._text = ""

    def set_frame_index(self, index: int) -> None:
        self._text = str(index)
        self.update()

    def clear(self) -> None:
        self._text = ""
        self.update()

    def paintEvent(self, event) -> None:
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_frame_number(painter, self._text, self.width())
        painter.end()


class _CommentOverlay(QWidget):
    """Read-only rendering of the current frame's saved strokes on top of
    the video surface — the plain viewer's (show_edit_tools=False)
    counterpart to DrawOverlay's live drawing canvas, added 2026-08-21 so
    UkoreShotPage's pushButton_show_comment_toggle can show what was drawn
    on a frame without opening the full CommentEditor. Reuses
    draw_overlay.py's own paint_stroke_points — the same normalized
    0-1-widget-space stroke math DrawOverlay/Get Video - Commented already
    use, just rendered here with none of DrawOverlay's mouse handling.
    WA_TransparentForMouseEvents, same as _FrameNumberOverlay — purely
    visual, never intercepts a click meant for the video surface beneath
    it. Only ever constructed for show_edit_tools=False (see PlayerWidget
    below) — edit mode already shows live strokes via DrawOverlay.load_frame,
    so it would just be a redundant second copy there."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._strokes: list[Stroke] = []
        self._show_enabled = False

    def set_strokes(self, strokes) -> None:
        self._strokes = list(strokes)
        self.update()

    def set_show_enabled(self, enabled: bool) -> None:
        self._show_enabled = enabled
        self.update()

    def paintEvent(self, event) -> None:
        if not self._show_enabled or not self._strokes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for stroke in self._strokes:
            paint_stroke_points(painter, stroke.points, QColor(stroke.color), stroke.width, self.width(), self.height())
        painter.end()


class _VideoStack(QWidget):
    """Manually layers `base` (the video surface), an optional `overlay`
    (DrawOverlay, revived 2026-08-20 for CommentEditor's edit-mode
    PlayerWidget — see `show_edit_tools` below), and `hud`
    (_FrameNumberOverlay) on top of each other, all filling this widget's
    whole area. The three-layer shape (base/overlay/hud) is the original
    one this class had before the whole draw/comment editor moved out to
    cache/plugins/BananaSketch/ 2026-08-08 (simplified to two layers then);
    `overlay=None` (the plain-viewer case) keeps that two-layer shape
    exactly. Still not `QStackedLayout(StackAll)` — see the host app's own
    developer/bug-history/2026-07-20-draw-overlay-native-video-widget.md for
    why that layout mode wasn't reliable here; explicit `setParent`/
    `show()`/`setGeometry`/`raise_()` is unambiguous and keeps `overlay`
    above `base` (so its mouse events land) with `hud` always topmost of
    all three (harmless — `hud` is `WA_TransparentForMouseEvents`, so it
    never steals a click meant for `overlay` beneath it)."""

    def __init__(self, base: QWidget, hud: QWidget, overlay: QWidget | None = None, parent=None):
        super().__init__(parent)
        self._base = base
        self._hud = hud
        self._overlay = overlay
        base.setParent(self)
        base.show()
        if overlay is not None:
            overlay.setParent(self)
            overlay.show()
            overlay.raise_()
        hud.setParent(self)
        hud.show()
        hud.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._base.setGeometry(0, 0, self.width(), self.height())
        if self._overlay is not None:
            self._overlay.setGeometry(0, 0, self.width(), self.height())
            self._overlay.raise_()
        self._hud.setGeometry(0, 0, self.width(), self.height())
        self._hud.raise_()


class TimelineSlider(QWidget):
    """Custom scrubber bar, added 2026-08-21 per the user's own request to
    replace the plain QSlider position_slider used to be: a plain QSlider
    only jumps to the exact click point when dragging the handle itself —
    clicking elsewhere on the groove instead moves by a single page step,
    which reads as "unresponsive" for a scrubber where you want to land
    exactly where you clicked. This widget instead computes the target
    value directly from the click/drag x position every time (see
    _value_at_x), so a single click anywhere on the bar snaps straight
    there. Also draws a tick per frame (skipped once frames would be
    packed closer than _MIN_TICK_SPACING_PX apart, which would otherwise
    just paint an unreadable solid smear) and a red marker line for any
    frame passed to set_marked_frames — used by comment_editor.py/
    video_library_page.py to show which frames already have a saved
    comment, revives the idea the pre-BananaSketch-extraction
    _CommentAwareSlider had for the same purpose (see slider_row's own
    history in PlayerWidget).

    Exposes the same value/range/signal surface a QSlider caller here
    needs (setRange/setValue/value/blockSignals — the last one is
    QObject's own, works unmodified) so _on_slider_pressed/_released/
    _moved and load_sequence's own setRange call don't need to change."""

    valueChanged = Signal(int)
    sliderPressed = Signal()
    sliderReleased = Signal()
    sliderMoved = Signal(int)

    _HEIGHT = 22
    _TRACK_COLOR = QColor("#3a3a3a")
    _FILLED_COLOR = QColor("#5865f2")
    _HANDLE_COLOR = QColor("#ffffff")
    _HANDLE_BORDER_COLOR = QColor("#222222")
    _TICK_COLOR = QColor(255, 255, 255, 40)
    _MARK_COLOR = QColor("#ff3b30")
    _MIN_TICK_SPACING_PX = 4
    _TRACK_THICKNESS = 6
    _HANDLE_RADIUS = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 0
        self._value = 0
        self._marked_frames: set[int] = set()
        self.setFixedHeight(self._HEIGHT)
        self.setMinimumWidth(40)

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = minimum
        self._maximum = max(minimum, maximum)
        self._value = max(self._minimum, min(self._value, self._maximum))
        self.update()

    def setValue(self, value: int) -> None:
        value = max(self._minimum, min(value, self._maximum))
        if value == self._value:
            return
        self._value = value
        self.update()

    def value(self) -> int:
        return self._value

    def set_marked_frames(self, frames) -> None:
        """frames: any iterable of frame indices to draw a red marker line
        for (e.g. comment_editor.py's own _keyframe_indices(), or
        video_library_page.py's _selected_entry_keyframe_indices())."""
        self._marked_frames = set(frames)
        self.update()

    def _value_at_x(self, x: float) -> int:
        span = max(1, self._maximum - self._minimum)
        width = max(1, self.width())
        fraction = max(0.0, min(1.0, x / width))
        return round(self._minimum + fraction * span)

    def _x_at_value(self, value: int) -> float:
        span = max(1, self._maximum - self._minimum)
        return (value - self._minimum) / span * self.width()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._maximum <= self._minimum:
            return
        self._value = self._value_at_x(event.position().x())
        self.update()
        self.sliderPressed.emit()
        self.sliderMoved.emit(self._value)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton) or self._maximum <= self._minimum:
            return
        self._value = self._value_at_x(event.position().x())
        self.update()
        self.sliderMoved.emit(self._value)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self.valueChanged.emit(self._value)
        self.sliderReleased.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        mid_y = self.height() / 2
        half_track = self._TRACK_THICKNESS / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._TRACK_COLOR)
        painter.drawRoundedRect(QRectF(0, mid_y - half_track, self.width(), self._TRACK_THICKNESS), half_track, half_track)

        span = self._maximum - self._minimum
        if span > 0:
            px_per_frame = self.width() / span
            if px_per_frame >= self._MIN_TICK_SPACING_PX:
                painter.setPen(QPen(self._TICK_COLOR, 1))
                for frame in range(self._minimum, self._maximum + 1):
                    x = self._x_at_value(frame)
                    painter.drawLine(QPointF(x, mid_y - half_track), QPointF(x, mid_y + half_track))

            filled_width = self._x_at_value(self._value)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._FILLED_COLOR)
            painter.drawRoundedRect(QRectF(0, mid_y - half_track, filled_width, self._TRACK_THICKNESS), half_track, half_track)

            if self._marked_frames:
                painter.setPen(QPen(self._MARK_COLOR, 2))
                for frame in self._marked_frames:
                    if self._minimum <= frame <= self._maximum:
                        x = self._x_at_value(frame)
                        painter.drawLine(QPointF(x, 1), QPointF(x, self.height() - 1))

        handle_x = self._x_at_value(self._value) if span > 0 else 0.0
        painter.setPen(QPen(self._HANDLE_BORDER_COLOR, 1))
        painter.setBrush(self._HANDLE_COLOR)
        painter.drawEllipse(QPointF(handle_x, mid_y), self._HANDLE_RADIUS, self._HANDLE_RADIUS)
        painter.end()


class PlayerWidget(QWidget):
    """Dual-source playback for whichever video/sequence UkoreShotPage's
    table (or CommentEditor) has selected. `load_video(path)` plays a real
    video file directly via QMediaPlayer, exactly as before — this stays
    the common path for casually browsing a freshly-playblasted video, per
    the "lazy conversion" decision (never force an image-sequence
    extraction just to look at something). `load_sequence(sequence_dir)`
    (added 2026-08-20, reviving the comment/drawing editor) instead drives
    a `SequencePlayer` over a already-extracted frame sequence — used by
    CommentEditor always (drawing/comments need an exact, machine-
    independent frame index, which QMediaPlayer's position-based estimate
    below can't guarantee across machines/codecs) and by UkoreShotPage only
    for a video that arrived purely via a pasted share code (no local video
    file exists for it at all). `_mode` tracks which source is currently
    active so every transport handler (play/pause/step/seek/speed) can
    dispatch to the right engine; UI/shortcuts/layout stay identical either
    way. Video-mode frame stepping/indexing is still an FPS-based
    approximation (round(position_ms / 1000 * fps)) — QMediaPlayer has no
    native frame-accurate API for arbitrary containers; confirmed with the
    user as an acceptable simplification ("แบบโง่") for that path.
    `_fps_value` (fixed at `_DEFAULT_FPS`, shown read-only via `fps_label`)
    drives video mode — no editable FPS control (removed 2026-07-20 per the
    user's own request: "we're not going to change the frame rate value
    anyway"); sequence mode instead uses whatever fps was recorded at
    extraction time (`SequencePlayer.fps`).

    **`show_edit_tools=True` also means "use `CommentEditor.ui`'s own
    transport row"**: `CommentEditor.ui` already lays out its
    own `pushButton_previous_frame`/`play`/`next_frame` (right beside its
    own Previous/Next Comment buttons), its own `pushButton_undo`/`redo`/
    `clear_frame`, and (2026-08-21) `pushButton_brush_color`/
    `comboBox_speed`/`lineEdit_keyframe` too — so this widget skips
    building/showing its own competing copies of any of those in that
    mode (own_transport_controls is also always False for CommentEditor —
    see below). The underlying handlers stay here (`step_frame`/
    `toggle_play`/`set_speed` are public wrappers `CommentEditor` connects
    its own buttons to; `draw_overlay.undo`/`redo`/`clear_frame`/
    `set_color` are already public) and `playingChanged` lets the host
    keep its own play button's icon in sync. The old code-built Select
    toggle/color swatch/size slider toolbar is gone entirely (removed
    2026-08-21 per the user's own request, not replaced by anything in
    this class — `CommentEditor` now owns color-picking itself via
    `pushButton_brush_color`; brush size stays adjustable via the mouse
    wheel or "F"-drag gesture over the canvas regardless, see
    draw_overlay.py; Select mode has no remaining trigger).

    `own_transport_controls=False` is the same idea for `UkoreShotPage.ui`'s
    own transport row (its own `pushButton_previous_frame`/`play`/
    `next_frame`, `lineEdit_keyframe`, `comboBox_speed`) — independent of
    `show_edit_tools` in principle, though both pages that exist today
    (`CommentEditor` and `UkoreShotPage`) now pass `own_transport_controls
    =False`. `step_frame`/`toggle_play`/`jump_to_frame`/`set_speed` are the
    public methods a page in this mode wires its own widgets to."""

    playingChanged = Signal(bool)
    frameIndexChanged = Signal(int)

    def __init__(self, parent=None, *, show_edit_tools: bool = False, own_transport_controls: bool = True):
        super().__init__(parent)
        self._video_path: Path | None = None
        self._scrubbing = False
        self._fps_value = _DEFAULT_FPS
        self._mode: str | None = None  # "video" | "sequence" | None
        self.show_edit_tools = show_edit_tools
        # own_transport_controls=False — added for UkoreShotPage.ui's own
        # transport row (pushButton_previous_frame/play/next_frame,
        # lineEdit_keyframe, comboBox_speed), independent of show_edit_tools
        # (which only ever gates the undo/redo/clear-frame + comment-nav
        # skip, still exclusive to CommentEditor — see that flag's own
        # note). When False, skips prev/play/next-frame *and*
        # goto_frame_spin *and* speed_slider from this widget's own layout
        # — the embedding page supplies all of those externally instead.
        # The widget objects themselves are still built either way (still
        # referenced internally, e.g. _set_paused_state's self.play_button),
        # just never added to a shown layout.
        self.own_transport_controls = own_transport_controls

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.video_sink = QVideoSink(self)
        self.player.setVideoSink(self.video_sink)
        self.video_sink.videoFrameChanged.connect(self._on_video_frame)
        self.video_surface = _VideoSurface()
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)

        self.sequence_player = SequencePlayer(self)
        self.sequence_player.frameChanged.connect(self._on_sequence_frame)
        self.sequence_player.playbackStateChanged.connect(lambda playing: self._set_paused_state(not playing))

        self.frame_number_overlay = _FrameNumberOverlay()
        # DrawOverlay revived 2026-08-20 for CommentEditor's edit-mode
        # PlayerWidget (show_edit_tools=True). A plain viewer
        # (show_edit_tools=False) gets _CommentOverlay instead — a
        # read-only rendering of the current frame's saved strokes, added
        # 2026-08-21 for UkoreShotPage's pushButton_show_comment_toggle —
        # the two are mutually exclusive (only one is ever non-None), both
        # fill the same _VideoStack "overlay" slot.
        self.draw_overlay = DrawOverlay() if show_edit_tools else None
        self.comment_overlay = None if show_edit_tools else _CommentOverlay()
        video_stack_widget = _VideoStack(
            self.video_surface, self.frame_number_overlay, self.draw_overlay or self.comment_overlay
        )

        self.play_button = QPushButton()
        self._set_button_icon(self.play_button, _PLAY_ICON_PATH, "Play")
        self.play_button.clicked.connect(self._on_play_clicked)
        self.prev_frame_button = QPushButton()
        self._set_button_icon(self.prev_frame_button, _PREV_FRAME_ICON_PATH, "<")
        self.prev_frame_button.clicked.connect(lambda: self._step_frame(-1))
        self.next_frame_button = QPushButton()
        self._set_button_icon(self.next_frame_button, _NEXT_FRAME_ICON_PATH, ">")
        self.next_frame_button.clicked.connect(lambda: self._step_frame(1))

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

        # Own row, separate from transport_row (below) — leftmost/rightmost
        # labels show the frame range. TimelineSlider (see above) as of
        # 2026-08-21 — click/drag-anywhere-snaps, per-frame grid, and red
        # marks for commented frames (set_comment_frames below), reviving
        # the idea the pre-BananaSketch-extraction _CommentAwareSlider had.
        self.start_frame_label = QLabel("0")
        self.end_frame_label = QLabel("0")
        self.position_slider = TimelineSlider()
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)

        slider_row = QHBoxLayout()
        slider_row.addWidget(self.start_frame_label)
        slider_row.addWidget(self.position_slider, stretch=1)
        slider_row.addWidget(self.end_frame_label)

        transport_row = QHBoxLayout()
        if own_transport_controls and not show_edit_tools:
            # In show_edit_tools mode, CommentEditor.ui already lays out its
            # own pushButton_previous_frame/play/next_frame right beside its
            # own Previous/Next Comment buttons — see the class docstring.
            # step_frame()/toggle_play() below are what CommentEditor wires
            # those .ui buttons to instead. own_transport_controls=False
            # (UkoreShotPage.ui's own transport row) skips these the same
            # way, independent of show_edit_tools.
            transport_row.addWidget(self.prev_frame_button)
            transport_row.addWidget(self.play_button)
            transport_row.addWidget(self.next_frame_button)
        if own_transport_controls:
            # goto_frame_spin/speed controls stay code-built even in
            # show_edit_tools mode (CommentEditor.ui has no equivalent for
            # either) — only own_transport_controls=False (UkoreShotPage.ui,
            # which supplies lineEdit_keyframe/comboBox_speed instead) skips
            # these.
            transport_row.addWidget(self.goto_frame_spin)
            transport_row.addWidget(self.fps_label)
        transport_row.addStretch()
        if own_transport_controls:
            transport_row.addWidget(QLabel("Speed"))
            transport_row.addWidget(self.speed_slider)
            transport_row.addWidget(self.speed_label)

        # -- edit shortcuts (show_edit_tools only) ---------------------------
        # Revived 2026-08-20 from the pre-2026-08-08 show_edit_tools=True
        # branch (see draw_overlay.py's own revival), then simplified
        # 2026-08-21 per the user's own request: brush/eraser are no longer
        # separate toolbar tools — left-click always draws, right-click
        # always erases (see DrawOverlay.mousePressEvent) — and the Text
        # tool is gone entirely. The Select toggle, color swatch, and size
        # slider that used to live in a code-built toolbar_row here were
        # removed entirely 2026-08-21 per the user's own request — color
        # picking moved to CommentEditor.ui's own pushButton_brush_color
        # (see comment_editor.py, which now owns _on_pick_color and connects
        # draw_overlay.colorPickRequested itself); brush size is still
        # adjustable without any dedicated widget via the mouse wheel or the
        # "F"-drag gesture (see draw_overlay.py's wheelEvent/_start_resize —
        # neither ever depended on size_slider existing); Select mode has no
        # remaining trigger and is effectively unreachable now, same as this
        # class's own removed toolbar. undo/redo/Clear moved to
        # CommentEditor.ui's own pushButton_undo/redo/clear_frame earlier the
        # same day, wired straight to draw_overlay there too. Ctrl+Z/
        # Ctrl+Shift+Z moved out of this class entirely 2026-08-21 (used to
        # live here, calling draw_overlay.undo/redo, which only ever tracked
        # the *current* frame's own strokes) — undo/redo is now a
        # CommentEditor-level, cross-frame concept spanning strokes *and*
        # comment edits, with each step jumping the player back to whichever
        # frame it happened on, so it belongs to CommentEditor's own action
        # history instead of this per-frame drawing canvas. See
        # comment_editor.py's _on_undo/_on_redo/_push_undo_snapshot.

        # "A"/"D" step one frame back/forward, "Space" toggles play/pause —
        # skipped while a text field has focus (goto_frame_spin's internal
        # line edit) via _is_typing() so typing those letters/space
        # doesn't hijack the cursor instead. Comment-jump ("Shift+A"/
        # "Shift+D") lives in comment_editor.py instead (this widget has no
        # keyframe table to jump between). Default Qt.WindowShortcut context
        # — only the old "1"-"4" tool-switch shortcuts are still gone, since
        # brush/eraser/text are no longer separate selectable tools at all
        # (see DrawOverlay's own docstring).
        self._prev_frame_shortcut = QShortcut(QKeySequence(Qt.Key_A), self)
        self._prev_frame_shortcut.activated.connect(lambda: self._step_frame_if_not_typing(-1))
        self._next_frame_shortcut = QShortcut(QKeySequence(Qt.Key_D), self)
        self._next_frame_shortcut.activated.connect(lambda: self._step_frame_if_not_typing(1))
        self._play_pause_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._play_pause_shortcut.activated.connect(self._toggle_play_if_not_typing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(video_stack_widget, stretch=1)
        layout.addLayout(slider_row)
        layout.addLayout(transport_row)

        self._set_paused_state(True)

    # -- loading --------------------------------------------------------

    def load_video(self, video_path: Path) -> None:
        _logger.debug("load_video(%s)", video_path)
        self._mode = "video"
        self.sequence_player.clear()
        self._video_path = video_path
        self.player.setSource(QUrl.fromLocalFile(str(video_path)))
        self.player.pause()

    def load_sequence(self, sequence_dir: Path) -> None:
        """Alternate entry point driving SequencePlayer instead of
        QMediaPlayer — see the class docstring for when each is used."""
        _logger.debug("load_sequence(%s)", sequence_dir)
        self._mode = "sequence"
        self._video_path = None
        self.player.stop()
        self.player.setSource(QUrl())
        if self.draw_overlay is not None:
            self.draw_overlay.clear_frame()
        self.sequence_player.load(sequence_dir)
        frame_count = self.sequence_player.frame_count
        self.position_slider.setRange(0, max(0, frame_count - 1))
        self.end_frame_label.setText(str(max(0, frame_count - 1)))
        self.goto_frame_spin.setRange(0, max(0, frame_count - 1))

    def clear_video(self) -> None:
        self._mode = None
        self._video_path = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.sequence_player.clear()
        if self.draw_overlay is not None:
            self.draw_overlay.clear_frame()
        if self.comment_overlay is not None:
            self.comment_overlay.set_strokes([])
        self.video_surface.clear_frame()
        self.frame_number_overlay.clear()

    def _on_video_frame(self, frame) -> None:
        if not frame.isValid():
            return
        image = frame.toImage()
        if not image.isNull():
            self.video_surface.set_frame(image)

    def _on_sequence_frame(self, frame_index: int, image: QImage) -> None:
        if not image.isNull():
            self.video_surface.set_frame(image)
        self.frame_number_overlay.set_frame_index(frame_index)
        if not self._scrubbing:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(frame_index)
            self.position_slider.blockSignals(False)
        self.goto_frame_spin.blockSignals(True)
        self.goto_frame_spin.setValue(frame_index)
        self.goto_frame_spin.blockSignals(False)
        self.frameIndexChanged.emit(frame_index)

    # -- transport --------------------------------------------------------

    def step_frame(self, delta: int) -> None:
        """Public wrapper around _step_frame — CommentEditor's own
        pushButton_previous_frame/next_frame (see class docstring) call
        this instead of PlayerWidget building its own duplicate buttons."""
        self._step_frame(delta)

    def toggle_play(self) -> None:
        """Public wrapper around _on_play_clicked — CommentEditor's own
        pushButton_play calls this instead."""
        self._on_play_clicked()

    def set_speed(self, rate: float) -> None:
        """Public wrapper around _on_speed_changed's core logic (0.01-1.00)
        — for a page supplying its own external speed control
        (own_transport_controls=False) instead of this widget's own
        speed_slider, e.g. UkoreShotPage.ui's comboBox_speed."""
        self._on_speed_changed(round(rate * 100))

    def _fps(self):
        return self.sequence_player.fps if self._mode == "sequence" else self._fps_value

    def _on_play_clicked(self) -> None:
        if self._mode == "sequence":
            if self.sequence_player.is_playing():
                self.sequence_player.pause()
            else:
                self._set_paused_state(False)
                self.sequence_player.play()
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self._set_paused_state(False)
            self.player.play()

    def _set_paused_state(self, paused: bool) -> None:
        self._set_button_icon(self.play_button, _PLAY_ICON_PATH if paused else _PAUSE_ICON_PATH, "Play" if paused else "Pause")
        self.playingChanged.emit(not paused)

    def _on_duration_changed(self, duration_ms: int) -> None:
        if self._mode != "video":
            return
        self.position_slider.setRange(0, max(0, duration_ms))
        total_frames = round(duration_ms / 1000 * self._fps())
        self.end_frame_label.setText(str(total_frames))
        self.goto_frame_spin.setRange(0, max(0, total_frames))

    def _on_position_changed(self, position_ms: int) -> None:
        if self._mode != "video":
            return
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self._set_paused_state(True)
        if not self._scrubbing:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position_ms)
            self.position_slider.blockSignals(False)
        frame_index = round(position_ms / 1000 * self._fps())
        self.frame_number_overlay.set_frame_index(frame_index)
        self.goto_frame_spin.blockSignals(True)
        self.goto_frame_spin.setValue(frame_index)
        self.goto_frame_spin.blockSignals(False)
        # Video mode never emitted this before — only sequence mode
        # (_on_sequence_frame) did. Needed so a page tracking "current
        # frame index" (e.g. UkoreShotPage's Previous/Next Comment) works
        # the same regardless of which mode is active; video mode's index
        # is still the same FPS-based approximation used everywhere else
        # in this class, not frame-exact.
        self.frameIndexChanged.emit(frame_index)

    def jump_to_frame(self, frame_index: int) -> None:
        """Public wrapper around _jump_to_frame — comment_editor.py uses
        this to jump the player when a keyframe comment table row is
        double-clicked."""
        self._jump_to_frame(frame_index)

    def set_comment_frames(self, frames) -> None:
        """Forwards to TimelineSlider.set_marked_frames — comment_editor.py
        calls this with its own _keyframe_indices() whenever self._frames
        changes; video_library_page.py calls it with
        _selected_entry_keyframe_indices() whenever the selected library
        row changes. PlayerWidget itself has no comment-data awareness —
        this is just a pass-through so callers don't need to reach into
        self.position_slider directly.

        frames is always a list of *frame* indices, but position_slider's
        own range/value are in milliseconds while in video mode (see
        _on_duration_changed/_on_position_changed) — sequence mode's is the
        only one already frame-indexed. Fixed 2026-08-21: this used to
        forward frames straight through unconverted, so in video mode
        (the common case in UkoreShotPage — any entry with a local video
        file not yet extracted to a sequence) every mark got plotted as if
        it were N *milliseconds* into a clip that's thousands of
        milliseconds long, clustering them all near the left edge.
        CommentEditor never hit this since it only ever uses load_sequence,
        never load_video."""
        if self._mode == "video":
            fps = self._fps()
            frames = [round(f / fps * 1000) for f in frames]
        self.position_slider.set_marked_frames(frames)

    def set_frame_strokes(self, strokes) -> None:
        """Feeds _CommentOverlay (view mode only) the current frame's saved
        strokes — video_library_page.py calls this on every frame change
        so the overlay always shows whichever frame is on screen. No-op in
        show_edit_tools mode (comment_overlay is None there; DrawOverlay
        already shows live strokes via load_frame instead)."""
        if self.comment_overlay is not None:
            self.comment_overlay.set_strokes(strokes)

    def set_comments_visible(self, visible: bool) -> None:
        """pushButton_show_comment_toggle — shows/hides _CommentOverlay
        without needing to re-supply set_frame_strokes' data each time."""
        if self.comment_overlay is not None:
            self.comment_overlay.set_show_enabled(visible)

    def _on_goto_frame_entered(self) -> None:
        self._jump_to_frame(self.goto_frame_spin.value())

    def _jump_to_frame(self, frame_index: int) -> None:
        if self._mode == "sequence":
            self.sequence_player.pause()
            self.sequence_player.seek(frame_index)
            return
        self.player.pause()
        self.player.setPosition(round(frame_index / self._fps() * 1000))

    def _on_slider_pressed(self) -> None:
        self._scrubbing = True

    def _on_slider_released(self) -> None:
        self._scrubbing = False
        if self._mode == "sequence":
            self.sequence_player.pause()
            self.sequence_player.seek(self.position_slider.value())
            return
        self.player.setPosition(self.position_slider.value())
        self.player.pause()

    def _on_slider_moved(self, value: int) -> None:
        if self._mode == "sequence":
            self.sequence_player.seek(value)
            return
        self.player.setPosition(value)

    def _on_speed_changed(self, value: int) -> None:
        rate = value / 100.0
        self.player.setPlaybackRate(rate)
        self.sequence_player.set_speed(rate)
        self.speed_label.setText(f"{rate:.2f}x")

    def _step_frame(self, delta: int) -> None:
        if self._mode == "sequence":
            self.sequence_player.step(delta)
            return
        self.player.pause()
        frame_ms = 1000 / self._fps()
        new_position = max(0, self.player.position() + delta * frame_ms)
        self.player.setPosition(round(new_position))

    def _is_typing(self) -> bool:
        """True while a text-input widget (goto_frame_spin's internal
        line edit) has focus — shared by every keyboard shortcut here so
        typing a letter/space that happens to match one doesn't hijack the
        cursor into stepping frames or toggling play instead."""
        focus_widget = QApplication.focusWidget()
        return isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit))

    def _step_frame_if_not_typing(self, delta: int) -> None:
        if not self._is_typing():
            self._step_frame(delta)

    def _toggle_play_if_not_typing(self) -> None:
        if not self._is_typing():
            self._on_play_clicked()

    # -- misc ---------------------------------------------------------

    @staticmethod
    def _set_button_icon(button, icon_path: Path, fallback_text: str) -> None:
        """Icon-only when icon_path exists on disk, text-only otherwise —
        so a button never renders as a blank, unclickable-looking square
        before the real data/icons/icons8-*.png file is placed. Works for
        both QToolButton (video_library_page.py's sort/view buttons, which
        reuse this method) and QPushButton (every icon button here)."""
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

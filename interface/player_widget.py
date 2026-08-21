from __future__ import annotations

import logging
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

from ukoreshot_plugin.interface.draw_overlay import TOOL_BRUSH, TOOL_ERASER, TOOL_SELECT, TOOL_TEXT, DrawOverlay
from ukoreshot_plugin.interface.sequence_player import SequencePlayer

_logger = logging.getLogger("UkoreShot.Player")

_MIN_TOOL_SIZE = 1
_MAX_TOOL_SIZE = 60
_DEFAULT_TOOL_SIZE = 6

_DEFAULT_FPS = 24
# cache/plugins/UkoreShot/interface/player_widget.py, one parent up is
# cache/plugins/UkoreShot/ itself (this plugin's own images/ folder, not
# the shared data/icons/ every other plugin uses — confirmed with the user
# 2026-07-21 that UkoreShot's icons live locally in the plugin instead, see
# images/README.md).
_ICONS_DIR = Path(__file__).resolve().parents[1] / "images"
_PREV_FRAME_ICON_PATH = _ICONS_DIR / "icons8-chevron-left-26.png"
_NEXT_FRAME_ICON_PATH = _ICONS_DIR / "icons8-right-26.png"
_PLAY_ICON_PATH = _ICONS_DIR / "icons8-play-50.png"
_PAUSE_ICON_PATH = _ICONS_DIR / "icons8-pause-50.png"
_EDIT_COMMENT_ICON_PATH = _ICONS_DIR / "icons8-edit-50.png"
# Not added to images/ yet — _set_button_icon falls back to a "Discord" text
# label until a real icon file is placed here, same as every other button's
# pre-icon state (see images/README.md's own note on this fallback).
_DISCORD_ICON_PATH = _ICONS_DIR / "icons8-discord-50.png"
_EDIT_COMMENT_BUTTON_SIZE = 32


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


class _FrameNumberOverlay(QWidget):
    """Always-on-top-right HUD showing the current frame number — white
    fill, black stroke, large bold text, per the user's own spec.
    WA_TransparentForMouseEvents so it never intercepts clicks meant for
    anything beneath it (the exact class of bug
    developer/bug-history/2026-07-20-draw-overlay-native-video-widget.md and
    2026-07-20-text-tool-drew-strokes-simultaneously.md were about, back
    when this plugin still had its own drawing overlay — moved to
    cache/plugins/BananaSketch/ 2026-08-08). No visibility toggle — always
    shown.

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

    `edit_comment_button` still just emits `editCommentRequested` — same
    signal-out, host-page-does-the-work split `sendToDiscordRequested`
    already uses, kept deliberately (this same widget is also embedded
    inside `CommentEditor` itself, section 9, where an "Edit Comment"
    button re-opening another editor from inside the editor wouldn't make
    sense — `CommentEditor` hides its own embedded instance's button
    instead of wiring the signal). `video_library_page.py`'s connection to
    this signal changed 2026-08-20: it now calls `ensure_sequence` then
    opens the in-house `CommentEditor` directly, replacing the old
    hand-off to the separate `BananaSketch` plugin."""

    editCommentRequested = Signal()
    sendToDiscordRequested = Signal()
    frameIndexChanged = Signal(int)

    def __init__(self, parent=None, *, show_edit_tools: bool = False):
        super().__init__(parent)
        self._video_path: Path | None = None
        self._scrubbing = False
        self._fps_value = _DEFAULT_FPS
        self._mode: str | None = None  # "video" | "sequence" | None
        self.show_edit_tools = show_edit_tools

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
        # PlayerWidget (show_edit_tools=True) — a plain viewer (the default)
        # gets no overlay at all, same two-layer _VideoStack shape as before.
        self.draw_overlay = DrawOverlay() if show_edit_tools else None
        video_stack_widget = _VideoStack(self.video_surface, self.frame_number_overlay, self.draw_overlay)

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
        # labels show the frame range. Plain QSlider now — used to carry
        # comment-frame tick marks (_CommentAwareSlider) before this
        # plugin stopped reading comment data entirely (moved to
        # cache/plugins/BananaSketch/ 2026-08-08).
        self.start_frame_label = QLabel("0")
        self.end_frame_label = QLabel("0")
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)

        slider_row = QHBoxLayout()
        slider_row.addWidget(self.start_frame_label)
        slider_row.addWidget(self.position_slider, stretch=1)
        slider_row.addWidget(self.end_frame_label)

        transport_row = QHBoxLayout()
        transport_row.addWidget(self.prev_frame_button)
        transport_row.addWidget(self.play_button)
        transport_row.addWidget(self.next_frame_button)
        transport_row.addWidget(self.goto_frame_spin)
        transport_row.addWidget(self.fps_label)
        transport_row.addStretch()
        transport_row.addWidget(QLabel("Speed"))
        transport_row.addWidget(self.speed_slider)
        transport_row.addWidget(self.speed_label)

        # edit_comment_button: a fixed 1:1 square icon button, emits
        # editCommentRequested for video_library_page.py to open
        # BananaSketch (UICommandService.navigate_and_focus) — used to open an
        # in-app EditVideoDialog before that editor moved out to its own
        # plugin 2026-08-08; the button's own meaning to an artist
        # ("Edit") hasn't changed, only what it opens. send_discord_button:
        # posts whichever video is loaded to the repo's configured Discord
        # channel — video_library_page.py connects sendToDiscordRequested
        # and owns the actual send (reading the channel/token, running
        # DiscordSendWorker), same division of labor as
        # edit_comment_button/editCommentRequested.
        self.edit_comment_button = QPushButton()
        self._set_button_icon(self.edit_comment_button, _EDIT_COMMENT_ICON_PATH, "Edit")
        self.edit_comment_button.setFixedSize(_EDIT_COMMENT_BUTTON_SIZE, _EDIT_COMMENT_BUTTON_SIZE)
        self.edit_comment_button.setEnabled(False)
        self.edit_comment_button.clicked.connect(self.editCommentRequested.emit)
        self.send_discord_button = QPushButton()
        self._set_button_icon(self.send_discord_button, _DISCORD_ICON_PATH, "Discord")
        self.send_discord_button.setToolTip("Send to Discord")
        self.send_discord_button.setEnabled(False)
        self.send_discord_button.clicked.connect(self.sendToDiscordRequested.emit)
        transport_row.addWidget(self.edit_comment_button)
        transport_row.addWidget(self.send_discord_button)
        if show_edit_tools:
            # Neither button means anything from inside the editor itself —
            # re-opening CommentEditor from within CommentEditor, or
            # sending a bare sequence-only PlayerWidget to Discord, are both
            # nonsensical here (see the class docstring).
            self.edit_comment_button.setVisible(False)
            self.send_discord_button.setVisible(False)

        # -- edit toolbar (show_edit_tools only) -----------------------------
        # Revived 2026-08-20 from the pre-2026-08-08 show_edit_tools=True
        # branch (see draw_overlay.py's own revival) — brush/eraser/text/
        # select tool buttons, a color swatch, a size slider, undo/redo,
        # and Clear, all driving self.draw_overlay directly.
        toolbar_row = None
        if show_edit_tools:
            self.brush_button = QToolButton()
            self.brush_button.setText("Brush")
            self.eraser_button = QToolButton()
            self.eraser_button.setText("Eraser")
            self.text_button = QToolButton()
            self.text_button.setText("Text")
            self.select_button = QToolButton()
            self.select_button.setText("Select")
            self._tool_buttons = {
                TOOL_BRUSH: self.brush_button,
                TOOL_ERASER: self.eraser_button,
                TOOL_TEXT: self.text_button,
                TOOL_SELECT: self.select_button,
            }
            self._tool_button_group = QButtonGroup(self)
            self._tool_button_group.setExclusive(True)
            for tool, button in self._tool_buttons.items():
                button.setCheckable(True)
                self._tool_button_group.addButton(button)
                button.clicked.connect(lambda checked, t=tool: self.draw_overlay.set_tool(t))
            self.brush_button.setChecked(True)
            self.draw_overlay.set_tool(TOOL_BRUSH)

            self.color_button = QPushButton()
            self.color_button.setFixedSize(24, 24)
            self._current_color = QColor("#ff3b30")
            self._update_color_button()
            self.color_button.clicked.connect(self._on_pick_color)

            self.size_slider = QSlider(Qt.Horizontal)
            self.size_slider.setRange(_MIN_TOOL_SIZE, _MAX_TOOL_SIZE)
            self.size_slider.setValue(_DEFAULT_TOOL_SIZE)
            self.size_slider.setMaximumWidth(100)
            self.size_slider.valueChanged.connect(self.draw_overlay.set_brush_width)
            self.draw_overlay.toolSizeChanged.connect(self.size_slider.setValue)
            self.draw_overlay.set_color(self._current_color)
            self.draw_overlay.set_brush_width(_DEFAULT_TOOL_SIZE)

            self.undo_button = QPushButton("Undo")
            self.undo_button.clicked.connect(self.draw_overlay.undo)
            self.redo_button = QPushButton("Redo")
            self.redo_button.clicked.connect(self.draw_overlay.redo)
            self.clear_button = QPushButton("Clear")
            self.clear_button.clicked.connect(self.draw_overlay.clear_frame)

            toolbar_row = QHBoxLayout()
            toolbar_row.addWidget(self.brush_button)
            toolbar_row.addWidget(self.eraser_button)
            toolbar_row.addWidget(self.text_button)
            toolbar_row.addWidget(self.select_button)
            toolbar_row.addWidget(self.color_button)
            toolbar_row.addWidget(QLabel("Size"))
            toolbar_row.addWidget(self.size_slider)
            toolbar_row.addStretch()
            toolbar_row.addWidget(self.undo_button)
            toolbar_row.addWidget(self.redo_button)
            toolbar_row.addWidget(self.clear_button)

        # "A"/"D" step one frame back/forward, "Space" toggles play/pause —
        # skipped while a text field has focus (goto_frame_spin's internal
        # line edit) via _is_typing() so typing those letters/space
        # doesn't hijack the cursor instead. Comment-jump ("Shift+A"/
        # "Shift+D") and draw-tool shortcuts (Ctrl+Z, "1"-"4") are gone —
        # this plugin has nothing left for them to act on.
        self._prev_frame_shortcut = QShortcut(QKeySequence(Qt.Key_A), self)
        self._prev_frame_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._prev_frame_shortcut.activated.connect(lambda: self._step_frame_if_not_typing(-1))
        self._next_frame_shortcut = QShortcut(QKeySequence(Qt.Key_D), self)
        self._next_frame_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._next_frame_shortcut.activated.connect(lambda: self._step_frame_if_not_typing(1))
        self._play_pause_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._play_pause_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._play_pause_shortcut.activated.connect(self._toggle_play_if_not_typing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if toolbar_row is not None:
            layout.addLayout(toolbar_row)
        layout.addWidget(video_stack_widget, stretch=1)
        layout.addLayout(slider_row)
        layout.addLayout(transport_row)

        self._set_paused_state(True)

    def _update_color_button(self) -> None:
        self.color_button.setStyleSheet("background-color: {}; border: 1px solid #888;".format(self._current_color.name()))

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(self._current_color, self, "Brush Color")
        if color.isValid():
            self._current_color = color
            self._update_color_button()
            self.draw_overlay.set_color(color)

    # -- loading --------------------------------------------------------

    def load_video(self, video_path: Path) -> None:
        _logger.debug("load_video(%s)", video_path)
        self._mode = "video"
        self.sequence_player.clear()
        self._video_path = video_path
        self.player.setSource(QUrl.fromLocalFile(str(video_path)))
        self.player.pause()
        self.edit_comment_button.setEnabled(True)
        self.send_discord_button.setEnabled(True)

    def load_sequence(self, sequence_dir: Path) -> None:
        """Alternate entry point driving SequencePlayer instead of
        QMediaPlayer — see the class docstring for when each is used.
        send_discord_button stays disabled: a sequence-only entry (no local
        video file) has nothing to send to Discord."""
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
        self.edit_comment_button.setEnabled(True)
        self.send_discord_button.setEnabled(False)

    def clear_video(self) -> None:
        self._mode = None
        self._video_path = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.sequence_player.clear()
        if self.draw_overlay is not None:
            self.draw_overlay.clear_frame()
        self.video_surface.clear_frame()
        self.frame_number_overlay.clear()
        self.edit_comment_button.setEnabled(False)
        self.send_discord_button.setEnabled(False)

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

    def jump_to_frame(self, frame_index: int) -> None:
        """Public wrapper around _jump_to_frame — comment_editor.py uses
        this to jump the player when a keyframe comment table row is
        double-clicked."""
        self._jump_to_frame(frame_index)

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

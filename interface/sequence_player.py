"""Image-sequence playback engine — drives PlayerWidget for a video that's
been split into a frame-numbered PNG/JPG sequence
(ukoreshot_plugin.core.video_sequence), instead of QMediaPlayer decoding a
video container directly. Used wherever frame-accurate stills are
guaranteed to exist: CommentEditor always (its sequence_dir was already
ensure_sequence()'d before the dialog opened, since drawing/comments need a
stable, machine-independent frame index — a QMediaPlayer position-based
frame estimate isn't guaranteed to land on the same frame on two different
machines/codecs), and UkoreShotPage's main viewer only for a video pulled in
purely by share code (core.share_sync.PullByCodeWorker), which has no local
video file to play back at all.

Frame images are read fresh off disk on every seek/step rather than
preloaded into memory — simplest correct thing for a review tool; a very
long sequence trades a bit of seek latency for not holding potentially
hundreds of decoded QImages resident at once."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from ukoreshot_plugin.core import comment_store

_DEFAULT_FPS = 24.0
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


class SequencePlayer(QObject):
    """frameChanged(frame_index, image) fires on every load()/seek()/step()/
    timer tick — PlayerWidget wires this the same way it used to wire
    QMediaPlayer.positionChanged, just with an exact integer frame index
    instead of an approximated one. playbackStateChanged(is_playing) fires
    on every play()/pause() *and* when playback naturally reaches the last
    frame and auto-pauses (mirrors QMediaPlayer.playbackState() ceasing to
    report PlayingState at end-of-media) — PlayerWidget needs this to keep
    its play/pause button icon in sync either way."""

    frameChanged = Signal(int, QImage)
    playbackStateChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame_paths: list[Path] = []
        self.fps: float = _DEFAULT_FPS
        self.current_frame_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._playing = False
        self._speed = 1.0

    @property
    def frame_count(self) -> int:
        return len(self.frame_paths)

    def load(self, sequence_dir: Path) -> None:
        self.pause()
        self.frame_paths = sorted(
            p for p in sequence_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
        )
        share = comment_store.get_share_state(sequence_dir)
        self.fps = share.get("fps") or _DEFAULT_FPS
        self.current_frame_index = 0
        self._emit_current_frame()

    def clear(self) -> None:
        self.pause()
        self.frame_paths = []
        self.current_frame_index = 0

    def is_playing(self) -> bool:
        return self._playing

    def set_speed(self, speed: float) -> None:
        """0.01x-1.00x, same range/meaning player_widget.py's speed_slider
        already exposes for QMediaPlayer.setPlaybackRate — restarts the
        timer at the new interval immediately if already playing."""
        self._speed = max(0.01, speed)
        if self._playing:
            self._timer.start(self._interval_ms())

    def _interval_ms(self) -> int:
        return max(1, round(1000 / (self.fps * self._speed)))

    def play(self) -> None:
        if not self.frame_paths:
            return
        self._playing = True
        self._timer.start(self._interval_ms())
        self.playbackStateChanged.emit(True)

    def pause(self) -> None:
        was_playing = self._playing
        self._playing = False
        self._timer.stop()
        if was_playing:
            self.playbackStateChanged.emit(False)

    def seek(self, frame_index: int) -> None:
        if not self.frame_paths:
            return
        self.current_frame_index = max(0, min(frame_index, len(self.frame_paths) - 1))
        self._emit_current_frame()

    def step(self, delta: int) -> None:
        self.pause()
        self.seek(self.current_frame_index + delta)

    def _advance(self) -> None:
        if self.current_frame_index >= len(self.frame_paths) - 1:
            self.pause()
            return
        self.current_frame_index += 1
        self._emit_current_frame()

    def _emit_current_frame(self) -> None:
        if not self.frame_paths:
            return
        image = QImage(str(self.frame_paths[self.current_frame_index]))
        self.frameChanged.emit(self.current_frame_index, image)

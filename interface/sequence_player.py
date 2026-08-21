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
hundreds of decoded QImages resident at once.

**Audio, added 2026-08-21**: a sequence_dir may also carry a
`<stem>.audio.m4a` (video_sequence.py's `extract_audio` — a no-op file
that simply doesn't exist for a silent source video, the common case for a
playblast). When present, an internal QMediaPlayer plays it back on
load()/play()/pause()/seek(), and _advance()'s timer tick polls its actual
.position() as the authoritative clock instead of blindly incrementing the
frame index by one — two independently-ticking clocks (an image-frame
QTimer and an audio QMediaPlayer) would otherwise slowly drift apart over
a long clip; polling one against the other every tick keeps them locked
together without needing a separate resync pass."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from ukoreshot_plugin.core import comment_store
from ukoreshot_plugin.core.video_sequence import audio_path_for

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
        self._has_audio = False
        self._audio_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_player.setAudioOutput(self._audio_output)

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
        audio_path = audio_path_for(sequence_dir, sequence_dir.name)
        self._has_audio = audio_path.is_file()
        self._audio_player.setSource(QUrl.fromLocalFile(str(audio_path)) if self._has_audio else QUrl())
        self._emit_current_frame()

    def clear(self) -> None:
        self.pause()
        self.frame_paths = []
        self.current_frame_index = 0
        self._has_audio = False
        self._audio_player.setSource(QUrl())

    def is_playing(self) -> bool:
        return self._playing

    def set_speed(self, speed: float) -> None:
        """0.01x-1.00x, same range/meaning player_widget.py's speed_slider
        already exposes for QMediaPlayer.setPlaybackRate — restarts the
        timer at the new interval immediately if already playing, and
        matches the audio player's own rate when a track is loaded so the
        two stay in step."""
        self._speed = max(0.01, speed)
        if self._playing:
            self._timer.start(self._interval_ms())
        if self._has_audio:
            self._audio_player.setPlaybackRate(self._speed)

    def _interval_ms(self) -> int:
        return max(1, round(1000 / (self.fps * self._speed)))

    def play(self) -> None:
        if not self.frame_paths:
            return
        self._playing = True
        self._timer.start(self._interval_ms())
        if self._has_audio:
            self._audio_player.setPlaybackRate(self._speed)
            self._audio_player.play()
        self.playbackStateChanged.emit(True)

    def pause(self) -> None:
        was_playing = self._playing
        self._playing = False
        self._timer.stop()
        if self._has_audio:
            self._audio_player.pause()
        if was_playing:
            self.playbackStateChanged.emit(False)

    def seek(self, frame_index: int) -> None:
        if not self.frame_paths:
            return
        self.current_frame_index = max(0, min(frame_index, len(self.frame_paths) - 1))
        if self._has_audio:
            self._audio_player.setPosition(round(self.current_frame_index / self.fps * 1000))
        self._emit_current_frame()

    def step(self, delta: int) -> None:
        self.pause()
        self.seek(self.current_frame_index + delta)

    def _advance(self) -> None:
        if self._has_audio:
            # The audio player is the authoritative clock while a track is
            # loaded — polled here (rather than driven by its own
            # positionChanged, which doesn't fire on every fps-aligned
            # tick) so the image sequence can't drift out of sync with
            # what's audible over a long clip.
            frame_index = round(self._audio_player.position() / 1000 * self.fps)
            frame_index = max(0, min(frame_index, len(self.frame_paths) - 1))
            if frame_index == self.current_frame_index:
                if self._audio_player.mediaStatus() == QMediaPlayer.EndOfMedia:
                    self.pause()
                return
            self.current_frame_index = frame_index
            self._emit_current_frame()
            return
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

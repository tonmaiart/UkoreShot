from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

_THUMBNAIL_HEIGHT = 68


class ThumbnailLoader(QObject):
    """Best-effort first-frame thumbnail extraction for UkoreShotPage's
    video list — one hidden QMediaPlayer + QVideoSink pair, requests
    processed one at a time (loading/seeking is all async, and running many
    of these in parallel is wasteful for what's just a list-row icon).
    Silently gives up on a video it can't decode a frame from — the list
    still shows the filename either way, just without an icon."""

    thumbnailReady = Signal(str, QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[Path] = []
        self._player: QMediaPlayer | None = None
        self._sink: QVideoSink | None = None
        self._current: Path | None = None
        self._captured = False

    def request(self, video_path: Path) -> None:
        self._queue.append(video_path)
        if self._current is None:
            self._process_next()

    def _process_next(self) -> None:
        if not self._queue:
            self._current = None
            return
        self._current = self._queue.pop(0)
        self._captured = False
        self._sink = QVideoSink(self)
        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.mediaStatusChanged.connect(self._on_status_changed)
        self._player.errorOccurred.connect(lambda *_args: self._finish_current())
        self._player.setSource(QUrl.fromLocalFile(str(self._current)))

    def _on_status_changed(self, status) -> None:
        if self._player is None:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._player.setPosition(min(200, max(0, self._player.duration() // 4)))
            self._player.play()
        elif status in (QMediaPlayer.MediaStatus.InvalidMedia, QMediaPlayer.MediaStatus.NoMedia):
            self._finish_current()

    def _on_frame(self, frame) -> None:
        if self._captured or not frame.isValid() or self._current is None:
            return
        image = frame.toImage()
        if not image.isNull():
            self._captured = True
            pixmap = QPixmap.fromImage(image).scaledToHeight(_THUMBNAIL_HEIGHT, Qt.SmoothTransformation)
            self.thumbnailReady.emit(str(self._current), pixmap)
            self._finish_current()

    def _finish_current(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._sink is not None:
            self._sink.deleteLater()
            self._sink = None
        self._current = None
        self._process_next()

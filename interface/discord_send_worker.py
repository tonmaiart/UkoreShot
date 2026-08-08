from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ukoreshot_plugin.core.discord_client import DiscordApiError, send_video


class DiscordSendWorker(QThread):
    """Same one-shot QThread-subclass shape as
    plugins/core/submit/status_worker.py's RepoStatusWorker — runs the
    (potentially slow, multi-MB upload) Discord POST off the UI thread."""

    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, token: str, channel_id: str, video_path: Path, message: str, parent=None):
        super().__init__(parent)
        self._token = token
        self._channel_id = channel_id
        self._video_path = video_path
        self._message = message

    def run(self) -> None:
        try:
            send_video(self._token, self._channel_id, self._video_path, self._message)
        except DiscordApiError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()

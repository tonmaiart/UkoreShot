from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ukoreshot_plugin.core.discord_client import DiscordApiError, find_or_create_forum_post, send_video
from ukoreshot_plugin.core.video_compress import VideoCompressionError, compress_to_fit, resolve_ffmpeg_path


class DiscordSendWorker(QThread):
    """Same one-shot QThread-subclass shape as
    plugins/core/submit/status_worker.py's RepoStatusWorker — runs the
    (potentially slow: a compression pass, a couple of thread-lookup
    calls, then a multi-MB upload) Discord requests off the UI thread.
    Takes the forum channel id + shot title rather than an
    already-resolved thread id, since resolving/creating the matching
    forum post is itself a network call that has to happen off the UI
    thread too — compress_to_fit, find_or_create_forum_post, and
    send_video all run here, in sequence."""

    succeeded = Signal()
    failed = Signal(str)

    def __init__(
        self,
        token: str,
        forum_channel_id: str,
        shot_title: str,
        video_path: Path,
        message: str,
        *,
        max_upload_bytes: int,
        ffmpeg_path: str | None,
        parent=None,
    ):
        super().__init__(parent)
        self._token = token
        self._forum_channel_id = forum_channel_id
        self._shot_title = shot_title
        self._video_path = video_path
        self._message = message
        self._max_upload_bytes = max_upload_bytes
        self._ffmpeg_path = ffmpeg_path

    def run(self) -> None:
        upload_path = self._video_path
        temp_dir: Path | None = None
        try:
            if self._video_path.stat().st_size > self._max_upload_bytes:
                ffmpeg_path = resolve_ffmpeg_path(self._ffmpeg_path)
                upload_path = compress_to_fit(ffmpeg_path, self._video_path, self._max_upload_bytes)
                if upload_path != self._video_path:
                    temp_dir = upload_path.parent
            thread_id = find_or_create_forum_post(self._token, self._forum_channel_id, self._shot_title)
            send_video(self._token, thread_id, upload_path, self._message)
        except (DiscordApiError, VideoCompressionError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

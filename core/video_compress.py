from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_AUDIO_BITRATE_BPS = 128_000
_MIN_VIDEO_BITRATE_BPS = 100_000  # below this the output isn't worth producing
_SIZE_SAFETY_MARGIN = 0.95  # leaves headroom for container/muxing overhead
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


class VideoCompressionError(Exception):
    """Raised with a message that's already safe to show the user."""


def resolve_ffmpeg_path(configured_path: str | None) -> str:
    """An explicit configured_path wins if it's a real file; otherwise
    falls back to whatever `ffmpeg` resolves to on this machine's PATH —
    same "explicit per-machine override, else PATH lookup" shape
    plugins/core/software_linker/ already uses for maya.exe. Raises
    VideoCompressionError up front (rather than letting a much-later,
    harder-to-diagnose subprocess failure surface) if neither resolves to
    a real executable."""
    if configured_path and Path(configured_path).is_file():
        return configured_path
    resolved = shutil.which("ffmpeg")
    if not resolved:
        raise VideoCompressionError(
            "ffmpeg isn't installed or isn't on this machine's PATH — install it, or set an explicit "
            "path under Repository Setting > UkoreShot."
        )
    return resolved


def _probe_duration_seconds(ffmpeg_path: str, video_path: Path) -> float:
    # ffmpeg always prints an input's own "Duration: HH:MM:SS.ms" line to
    # stderr when given -i, even with no output specified (which makes it
    # exit non-zero afterward) — good enough to read the duration off of
    # without requiring a separate ffprobe binary/lookup.
    result = subprocess.run(
        [ffmpeg_path, "-i", str(video_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    match = _DURATION_PATTERN.search(result.stderr)
    if not match:
        raise VideoCompressionError(
            f"Couldn't read {video_path.name}'s duration (ffmpeg didn't report one) — can't calculate a "
            "compression target."
        )
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def compress_to_fit(ffmpeg_path: str, video_path: Path, max_bytes: int) -> Path:
    """Transcodes video_path down to a temp .mp4 that fits under max_bytes,
    at a single-pass bitrate calculated from its duration — good enough to
    land close to the target, not frame-accurate like a proper two-pass
    encode would be, which would roughly double the encode time for a use
    case (fitting under Discord's upload cap) that doesn't need that
    precision. Returns video_path unchanged if it's already under
    max_bytes (no transcode at all) — the caller should only clean up the
    returned path's parent directory when it's *not* video_path itself."""
    if video_path.stat().st_size <= max_bytes:
        return video_path

    duration = _probe_duration_seconds(ffmpeg_path, video_path)
    if duration <= 0:
        raise VideoCompressionError(
            f"{video_path.name} reported a zero-length duration — can't calculate a compression target."
        )

    target_bits = max_bytes * 8 * _SIZE_SAFETY_MARGIN
    video_bitrate = int(target_bits / duration) - _AUDIO_BITRATE_BPS
    if video_bitrate < _MIN_VIDEO_BITRATE_BPS:
        raise VideoCompressionError(
            f"{video_path.name} is too long ({duration:.0f}s) to fit under the configured upload limit even "
            "at the lowest usable quality — trim it, or raise Max Upload Size if the Discord server allows it."
        )

    output_path = Path(tempfile.mkdtemp(prefix="ukorehub_discord_")) / f"{video_path.stem}_compressed.mp4"
    result = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-i", str(video_path),
            "-b:v", str(video_bitrate),
            "-b:a", str(_AUDIO_BITRATE_BPS),
            "-movflags", "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0 or not output_path.is_file():
        raise VideoCompressionError(f"ffmpeg failed to compress {video_path.name}: {result.stderr[-500:]}")
    return output_path

"""Lazy video->PNG-sequence extraction — the desktop side's own job now
(confirmed with the user 2026-08-20; UkorePlayblast used to write a sequence
itself alongside every Video-mode playblast, removed the same day — see
maya-scripts/README.md's "Image sequence generation moved to the desktop
side"). Mirrors video_compress.py's shape (reuses its resolve_ffmpeg_path
directly, same per-repo-configured-path-else-PATH-lookup convention) since
this is the same "shell out to ffmpeg" pattern, just extracting frames
instead of transcoding.

Deliberately lazy, not run for every video on every library reload: only
called from video_library_page.py's Comment and Mark as Share handlers,
right before each needs frame-accurate stills to exist — confirmed with the
user this round specifically to avoid ffmpeg work for a playblast that's
just being casually browsed."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from ukoreshot_plugin.core.video_compress import VideoCompressionError, resolve_ffmpeg_path  # noqa: F401

_logger = logging.getLogger("UkoreShot.VideoSequence")

_FPS_PATTERN = re.compile(r"([\d.]+)\s*fps")
_DEFAULT_FPS = 24.0
_FRAME_GLOB_PATTERN = re.compile(r"^(.+)\.(\d{5})\.[^.]+$")


def sequence_dir_for(video_path: Path) -> Path:
    """<video_root>/<stem>/ — same folder convention UkorePlayblast used to
    write its own sequence into before 2026-08-20, so an old on-disk layout
    from before this change still lines up with a fresh extraction here."""
    return video_path.parent / video_path.stem


def has_sequence(video_path: Path) -> bool:
    """Quick existence check, no ffmpeg call — a numbered frame file already
    present means ensure_sequence() has nothing to do."""
    sequence_dir = sequence_dir_for(video_path)
    if not sequence_dir.is_dir():
        return False
    stem = video_path.stem
    return any(_FRAME_GLOB_PATTERN.match(p.name) and p.name.startswith(stem + ".") for p in sequence_dir.iterdir())


def probe_fps(ffmpeg_path: str, video_path: Path) -> float:
    """Parses the "NN.NN fps" token off ffmpeg's own -i-with-no-output
    stderr dump — same technique video_compress.py's _probe_duration_seconds
    already uses for that stream's "Duration:" line. Falls back to
    _DEFAULT_FPS (matches player_widget.py's own long-standing hardcoded
    default) rather than raising — a missing/unparseable fps shouldn't block
    extraction, just make frame-stepping timing a best guess."""
    result = subprocess.run(
        [ffmpeg_path, "-i", str(video_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    match = _FPS_PATTERN.search(result.stderr)
    if not match:
        return _DEFAULT_FPS
    try:
        return float(match.group(1))
    except ValueError:
        return _DEFAULT_FPS


def extract_sequence(
    ffmpeg_path: str, video_path: Path, output_dir: Path, *, image_format: str = "png"
) -> tuple[int, float]:
    """Runs ffmpeg -vsync 0 (one output file per source frame, no
    duplicate/dropped-frame retiming) writing <stem>.%05d.<image_format>
    into output_dir. Returns (frame_count, fps). Raises
    VideoCompressionError (reused rather than a new exception type — same
    "ffmpeg failed" shape video_compress.py already has a caller-facing
    error class for) on failure or zero frames produced."""
    _logger.info("extract_sequence: %s -> %s (ffmpeg=%s)", video_path, output_dir, ffmpeg_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    fps = probe_fps(ffmpeg_path, video_path)
    pattern = output_dir / "{}.%05d.{}".format(video_path.stem, image_format)
    result = subprocess.run(
        [ffmpeg_path, "-y", "-i", str(video_path), "-vsync", "0", str(pattern)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    frame_count = sum(
        1
        for p in output_dir.iterdir()
        if p.name.startswith(video_path.stem + ".") and p.suffix == "." + image_format
    )
    if result.returncode != 0 or frame_count == 0:
        _logger.error(
            "ffmpeg failed (returncode=%s, frame_count=%s) for %s: %s",
            result.returncode, frame_count, video_path, result.stderr[-2000:],
        )
        raise VideoCompressionError(
            "ffmpeg failed to extract an image sequence from {}: {}".format(video_path.name, result.stderr[-500:])
        )
    _logger.info("extract_sequence finished: %d frame(s), fps=%s", frame_count, fps)
    return frame_count, fps


def ensure_sequence(ffmpeg_path: str, video_path: Path, *, image_format: str = "png") -> Path:
    """The one function callers actually use — no-op if has_sequence() is
    already true (whether from a prior extraction or an old
    UkorePlayblast-written sequence predating 2026-08-20), else extracts.
    Returns the sequence_dir either way."""
    sequence_dir = sequence_dir_for(video_path)
    if not has_sequence(video_path):
        extract_sequence(ffmpeg_path, video_path, sequence_dir, image_format=image_format)
    return sequence_dir

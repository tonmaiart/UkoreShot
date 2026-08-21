from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

_logger = logging.getLogger("UkoreShot.VideoCompress")

_AUDIO_BITRATE_BPS = 128_000
_MIN_VIDEO_BITRATE_BPS = 100_000  # below this the output isn't worth producing
_SIZE_SAFETY_MARGIN = 0.95  # leaves headroom for container/muxing overhead
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")

# Was a git-tracked bin/ffmpeg.exe (win64 GPL static build) bundled directly
# in this repo from 2026-08-21 — removed the same day after it blocked
# `git push` outright (145MB, over GitHub's 100MB hard file-size limit).
# Replaced with this: a per-machine, on-demand download into UkoreHub's own
# gitignored cache dir instead of a git-tracked binary, using the same
# BtbN/FFmpeg-Builds win64-gpl asset that used to be bundled (same source/
# license, just fetched once per machine instead of shipped in git history).
_FFMPEG_DOWNLOAD_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"


class VideoCompressionError(Exception):
    """Raised with a message that's already safe to show the user."""


_FFMPEG_PATH_KEY = "ffmpeg_path"


def get_ffmpeg_path(api) -> str | None:
    """Per-machine (shared=False, gitignored) explicit ffmpeg override —
    moved here from the now-deleted core/discord_client.py (this plugin no
    longer talks to Discord at all). None/blank means "look up ffmpeg via
    resolve_ffmpeg_path's cache-download/PATH fallback below"."""
    store = api.plugin_config_store("ukore_shot", shared=False)
    return store.get(_FFMPEG_PATH_KEY) or None


def set_ffmpeg_path(api, ffmpeg_path: str | None) -> None:
    store = api.plugin_config_store("ukore_shot", shared=False)
    store.set(_FFMPEG_PATH_KEY, ffmpeg_path or None)


def _cached_ffmpeg_path(cache_dir: Path) -> Path:
    return cache_dir / "ukore_shot" / "bin" / "ffmpeg.exe"


def _download_ffmpeg(dest_path: Path) -> None:
    """Downloads BtbN's win64 GPL ffmpeg build straight into dest_path,
    extracting only the single bin/ffmpeg.exe member from the zip
    (ffplay/ffprobe are never needed here). Downloads to sibling temp files
    first and only replaces dest_path once the extraction fully succeeds,
    so a failed/interrupted download can never leave a half-written
    ffmpeg.exe behind for a later resolve_ffmpeg_path call to trip over."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = dest_path.parent / "ffmpeg_download.zip.tmp"
    tmp_exe = dest_path.parent / "ffmpeg.exe.tmp"
    _logger.info("downloading ffmpeg from %s", _FFMPEG_DOWNLOAD_URL)
    try:
        urllib.request.urlretrieve(_FFMPEG_DOWNLOAD_URL, tmp_zip)
        with zipfile.ZipFile(tmp_zip) as zf:
            member = next((n for n in zf.namelist() if n.replace("\\", "/").endswith("/bin/ffmpeg.exe")), None)
            if member is None:
                raise VideoCompressionError("Downloaded ffmpeg archive didn't contain a bin/ffmpeg.exe member.")
            with zf.open(member) as src, open(tmp_exe, "wb") as dst:
                shutil.copyfileobj(src, dst)
        tmp_exe.replace(dest_path)
        _logger.info("ffmpeg downloaded to %s", dest_path)
    finally:
        tmp_zip.unlink(missing_ok=True)
        tmp_exe.unlink(missing_ok=True)


def resolve_ffmpeg_path(configured_path: str | None, cache_dir: Path) -> str:
    """An explicit configured_path wins if it's a real file (a studio-wide
    override under Repository Setting > UkoreShot still makes sense — a
    pinned/newer ffmpeg build, or a non-Windows machine the downloaded
    win64 .exe can't run on); otherwise a previously-downloaded copy
    already cached under cache_dir/ukore_shot/bin/ffmpeg.exe; otherwise a
    fresh download of that same win64 build straight into that cache path
    (Windows only — see _FFMPEG_DOWNLOAD_URL); otherwise whatever `ffmpeg`
    resolves to on this machine's PATH (kept as a last-resort fallback in
    case the download ever fails, e.g. no internet) — same "explicit
    per-machine override, else built-in, else PATH lookup" shape
    plugins/core/software_linker/ uses for maya.exe, just with an
    on-demand download as the new middle tier instead of a bundled binary.
    Raises VideoCompressionError up front (rather than letting a
    much-later, harder-to-diagnose subprocess failure surface) if none of
    these resolves to a real executable."""
    if configured_path and Path(configured_path).is_file():
        return configured_path
    cached = _cached_ffmpeg_path(cache_dir)
    if cached.is_file():
        return str(cached)
    if platform.system() == "Windows":
        try:
            _download_ffmpeg(cached)
            return str(cached)
        except Exception as exc:  # noqa: BLE001 - falls through to a PATH lookup below
            _logger.warning("ffmpeg auto-download failed: %s", exc)
    resolved = shutil.which("ffmpeg")
    if not resolved:
        raise VideoCompressionError(
            "ffmpeg isn't installed or isn't on this machine's PATH, and the automatic ffmpeg download failed "
            "(check your internet connection) — or set an explicit path under Repository Setting > UkoreShot."
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


def _calculate_video_bitrate(video_path: Path, max_bytes: int, duration: float) -> int:
    """Shared by compress_to_fit and burn_frame_numbers — same single-pass,
    duration-based target-bitrate math both use to land close to
    max_bytes, not frame-accurate like a proper two-pass encode would be
    (which would roughly double encode time for a use case that doesn't
    need that precision)."""
    target_bits = max_bytes * 8 * _SIZE_SAFETY_MARGIN
    video_bitrate = int(target_bits / duration) - _AUDIO_BITRATE_BPS
    if video_bitrate < _MIN_VIDEO_BITRATE_BPS:
        raise VideoCompressionError(
            f"{video_path.name} is too long ({duration:.0f}s) to fit under the configured upload limit even "
            "at the lowest usable quality — trim it, or raise Max Upload Size if the Discord server allows it."
        )
    return video_bitrate


def compress_to_fit(ffmpeg_path: str, video_path: Path, max_bytes: int) -> Path:
    """Transcodes video_path down to a temp .mp4 that fits under max_bytes,
    at a single-pass bitrate calculated from its duration (see
    _calculate_video_bitrate). Returns video_path unchanged if it's
    already under max_bytes (no transcode at all) — the caller should only
    clean up the returned path's parent directory when it's *not*
    video_path itself."""
    if video_path.stat().st_size <= max_bytes:
        return video_path

    duration = _probe_duration_seconds(ffmpeg_path, video_path)
    if duration <= 0:
        raise VideoCompressionError(
            f"{video_path.name} reported a zero-length duration — can't calculate a compression target."
        )
    video_bitrate = _calculate_video_bitrate(video_path, max_bytes, duration)

    output_path = Path(tempfile.mkdtemp(prefix="ukorehub_discord_")) / f"{video_path.stem}_compressed.mp4"
    result = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-i", str(video_path),
            # Explicit stream mapping — without it, ffmpeg's default
            # "best stream per type" selection dropped the audio track on
            # some Maya-playblasted .mov sources (Get Video came out
            # silent even though the source had sound). "0:a:0?" the "?"
            # makes an audio stream optional, so a genuinely silent source
            # still encodes fine with video only.
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-b:v", str(video_bitrate),
            "-b:a", str(_AUDIO_BITRATE_BPS),
            # veryfast, not the libx264 default (medium) — this is a quick
            # local export, not a mastered deliverable, so trading a little
            # compression efficiency for noticeably faster encoding is the
            # right call (2026-08-21, per the user's own request to speed
            # Get Video up).
            "-preset", "veryfast",
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


# Windows ffmpeg builds have no fontconfig (that's a Unix font-matching
# library) — drawtext needs an explicit fontfile= path to a real .ttf,
# rather than resolving a font by name the way it can on Linux/macOS.
# Arial Bold ships with every Windows install, matching this plugin's
# other Windows-only conventions (the win64 ffmpeg auto-download,
# explorer /select, reveal — see this module's own _FFMPEG_DOWNLOAD_URL).
_FRAME_NUMBER_FONT_PATH = r"C:\Windows\Fonts\arialbd.ttf"


def _escape_ffmpeg_filter_path(path: str) -> str:
    """ffmpeg's filtergraph mini-language uses ":" to separate a filter's
    own options and "\\" as its escape character, so a raw Windows path
    (drive-letter colon, backslash separators) can't be dropped into a
    filter option value as-is. Forward slashes work fine on Windows ffmpeg
    builds too, so converting to those sidesteps needing to escape every
    backslash — only the drive-letter colon still needs an explicit
    escape."""
    return path.replace("\\", "/").replace(":", r"\:")


def burn_frame_numbers(ffmpeg_path: str, video_path: Path, max_bytes: int) -> Path:
    """Re-encodes video_path with the current frame number burned into
    every frame, top-center, white fill with a black outline (drawtext's
    own bordercolor/borderw — visually close to player_widget.py's
    _FrameNumberOverlay/paint_frame_number's own stamped-offsets
    technique, without needing a separate per-frame Qt compositing pass
    the way Get Video - Commented's already frame-by-frame pipeline uses
    instead — see video_library_page.py's _render_commented_video for
    that one). "%{frame_num}" is drawtext's own current-output-frame-number
    expansion keyword, 0-based, matching every other "frame index" already
    used throughout this codebase — not "%{n}" (fixed 2026-08-21 after a
    real "Error parsing a filter description" failure: "n" isn't a
    documented drawtext expansion function, "frame_num" is).

    Also applies the same duration-based target bitrate compress_to_fit
    uses (_calculate_video_bitrate), in this same ffmpeg call, instead of
    a separate compress_to_fit() pass afterward (2026-08-21 — the original
    two-pass version made Get Video noticeably slower, since burning text
    already forces one full re-encode regardless of the source's own
    size; targeting max_bytes in that same pass means there's only ever
    one re-encode total, with the same "fits under max_bytes" guarantee
    compress_to_fit's own two-step version had). Always re-encodes, even
    for a source already small enough that compress_to_fit alone would've
    just copied unchanged — there's no way to burn text into a video
    without decoding/re-encoding every frame. Returns a path into a fresh
    temp directory; the caller owns cleaning it up."""
    duration = _probe_duration_seconds(ffmpeg_path, video_path)
    if duration <= 0:
        raise VideoCompressionError(
            f"{video_path.name} reported a zero-length duration — can't calculate a compression target."
        )
    video_bitrate = _calculate_video_bitrate(video_path, max_bytes, duration)

    drawtext = (
        "drawtext=fontfile={}:text='%{{frame_num}}':fontcolor=white:fontsize=48:"
        "bordercolor=black:borderw=5:x=(w-text_w)/2:y=20"
    ).format(_escape_ffmpeg_filter_path(_FRAME_NUMBER_FONT_PATH))
    output_path = Path(tempfile.mkdtemp(prefix="ukorehub_framenum_")) / f"{video_path.stem}_framenum.mp4"
    result = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-i", str(video_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-vf", drawtext,
            "-b:v", str(video_bitrate),
            "-b:a", str(_AUDIO_BITRATE_BPS),
            "-preset", "veryfast",
            "-movflags", "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0 or not output_path.is_file():
        raise VideoCompressionError(
            f"ffmpeg failed to burn frame numbers into {video_path.name}: {result.stderr[-500:]}"
        )
    return output_path

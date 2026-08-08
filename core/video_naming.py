from __future__ import annotations

import re
from pathlib import Path

# Mirrors UkorePlayblast/maya-scripts/UkorePlayblast/function.py's own
# _FILENAME_PATTERN exactly (two separate Python environments — Maya's
# Python can't import this desktop-side plugins/ package, same reason
# every other "construct straight off disk" duplication in this codebase
# exists) — SEQ_ShotCode_Variation_index_version, see that plugin's
# README for the full naming convention.
_FILENAME_PATTERN = re.compile(r"^([^_]+)_([^_]+)_([^_]+)_(\d+)_v(\d+)$")


def parse_video_filename(video_path: Path) -> dict | None:
    """{"sequence", "shot_code", "variation", "index", "version"} parsed
    off video_path's filename stem, or None if it doesn't match
    UkorePlayblast's flat naming convention — a playblast from before
    2026-07-20 still sitting in its old `<sequence>/<shot_code>/vNNN/`
    subfolder (left alone there per the user's own decision, see
    UkorePlayblast/README.md's "Pre-2026-07-20 shot/version subfolders"),
    or any other file that just doesn't happen to follow this convention.
    filter_sidebar.py and video_library_page.py bucket a None result under
    "Unknown" rather than erroring or hiding the video — it still plays
    and can still be commented on, it just can't be filtered/sorted by
    these fields."""
    match = _FILENAME_PATTERN.match(video_path.stem)
    if not match:
        return None
    sequence, shot_code, variation, index_str, version_str = match.groups()
    return {
        "sequence": sequence,
        "shot_code": shot_code,
        "variation": variation,
        "index": int(index_str),
        "version": int(version_str),
    }

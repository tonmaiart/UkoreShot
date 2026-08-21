"""Per-video comment/drawing/share metadata — revived 2026-08-20 from the
old plugins/repo_internal/UkoreShot/core/comment_store.py (extracted out to
the separate BananaSketch plugin on 2026-08-08, brought back in-house per
the user's own request; see git history at commit f9b3505 in UkoreHubDev
for the original). Two differences from the original: this file now lives
at <sequence_dir>/comments.json (a video's own core/video_sequence.py
extraction folder) instead of a <video>.ukoreshot.json sidecar next to the
video file itself — matches the user's instruction that comment data lives
in the same folder as the image sequence — and it gains a top-level "share"
block (see get_share_state/set_share_state) so Mark as Share/the
search-bar share-code round-trip (core/share_sync.py) have everything they
need without ever having to *list* what's in the cloud bucket."""

from __future__ import annotations

import getpass
import json
import logging
import uuid
from pathlib import Path

_logger = logging.getLogger("UkoreShot.CommentStore")

_FILENAME = "comments.json"

_DEFAULT_SHARE_STATE = {
    "is_shared": False,
    "code": None,
    "shared_at": None,
    "frame_count": None,
    "image_format": None,
    "fps": None,
}


def metadata_path(sequence_dir: Path) -> Path:
    return sequence_dir / _FILENAME


def current_username(api) -> str:
    """Best-effort commenter identity for the comment table — the cached
    GitHub username (api.local_config.github_username, the documented
    plugin_api-sanctioned shared instance, NOT a raw LocalConfigStore
    constructed straight off disk — that convenience only exists for
    Maya-side modules with no `api` handle at all, which this desktop-only
    file always has via its caller) if logged in, else the OS account name,
    so commenting still works for a machine that's never done GitHub
    login."""
    if api is not None and getattr(api.local_config, "github_username", None):
        return api.local_config.github_username
    return getpass.getuser()


def load(sequence_dir: Path) -> dict:
    """{"frames": {...}, "share": {...}} — returns fresh defaults for both
    keys if no file exists yet, or it fails to parse (a corrupt/foreign file
    shouldn't crash the viewer)."""
    path = metadata_path(sequence_dir)
    if not path.is_file():
        return {"frames": {}, "share": dict(_DEFAULT_SHARE_STATE)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _logger.warning("could not read/parse %s — treating as empty", path, exc_info=True)
        return {"frames": {}, "share": dict(_DEFAULT_SHARE_STATE)}
    if not isinstance(data, dict):
        return {"frames": {}, "share": dict(_DEFAULT_SHARE_STATE)}
    frames = data.get("frames")
    if not isinstance(frames, dict):
        frames = {}
    share = dict(_DEFAULT_SHARE_STATE)
    if isinstance(data.get("share"), dict):
        share.update(data["share"])
    return {"frames": frames, "share": share}


def save(sequence_dir: Path, data: dict) -> None:
    """Whole-file rewrite, same as the original — data must already be the
    full {"frames": ..., "share": ...} shape load() returns."""
    metadata_path(sequence_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_share_state(sequence_dir: Path) -> dict:
    return load(sequence_dir)["share"]


def set_share_state(sequence_dir: Path, **fields) -> None:
    """Updates only the given fields of the "share" block, leaving "frames"
    (and any other share field not passed) untouched."""
    data = load(sequence_dir)
    data["share"].update(fields)
    save(sequence_dir, data)


def generate_share_code(shot_code: str, version: int) -> str:
    """{ShotCode}_v{version}_{4-char code} — generated once on first Mark as
    Share and persisted via set_share_state(code=...); never regenerated on
    a later re-share of the same video. uuid.uuid4().hex[:4] is the same
    "short random suffix" convention already used elsewhere in this
    codebase (comment ids, options_store.py's variation sanitizing)."""
    return "{}_v{:03d}_{}".format(shot_code, version, uuid.uuid4().hex[:4].upper())

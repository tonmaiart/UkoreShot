from __future__ import annotations

import getpass
import json
from pathlib import Path

# The app's own top-level core/ package (core/store.py), NOT this file's
# sibling ukoreshot_plugin.core package — same name, different
# package, resolved unambiguously since this is an absolute import (Python
# resolves it from sys.path's repo root, not relative to this file's own
# location) — see cache/plugins/UkoreShot/core/README.md's note on this.
from core.store import LocalConfigStore

_SUFFIX = ".ukoreshot.json"
# cache/plugins/UkoreShot/core/comment_store.py, four parents up is the
# UkoreHub repo root (same depth/pattern as player_widget.py's
# _ICONS_DIR) — no `api` handle available this deep.
_REPO_ROOT = Path(__file__).resolve().parents[4]


def sidecar_path(video_path: Path) -> Path:
    return video_path.with_name(video_path.name + _SUFFIX)


def current_username() -> str:
    """Best-effort commenter identity for CommentThread's Facebook-style
    multi-user comments (added 2026-07-20) — the cached GitHub username
    (LocalConfigStore, constructed straight off disk the same way every
    Maya-side/deep-desktop module in this codebase does without an `api`
    handle) if logged in, else the OS account name, so commenting still
    works for a machine that's never done GitHub login."""
    store = LocalConfigStore(_REPO_ROOT / "data" / "local_config.json")
    if store.github_username:
        return store.github_username
    return getpass.getuser()


def load(video_path: Path) -> dict:
    """{"frames": {"<frame_index>": {"strokes": [...], "comments": [...]}}} —
    saved next to the video itself in the resolved library folder (see
    video_path_store.resolve_video_root) so it travels with the shared
    library, no separate app data store needed. `"comments"` (added
    2026-07-20, see comment_thread.py) is a list of
    `{"id", "author", "text", "timestamp"}` — multiple users can comment on
    the same frame, Facebook-style, each individually deletable; replaces
    the old single `"note"` string field, which a frame saved before
    2026-07-20 may still have (comment_thread.py's CommentThread migrates
    it into a one-item comments list on load — see PlayerWidget._load_current_frame).
    Returns {"frames": {}} if no sidecar exists yet, or it fails to parse
    (a corrupt/foreign file next to a video shouldn't crash the viewer)."""
    path = sidecar_path(video_path)
    if not path.is_file():
        return {"frames": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"frames": {}}
    if not isinstance(data, dict) or not isinstance(data.get("frames"), dict):
        return {"frames": {}}
    return data


def save(video_path: Path, frames: dict) -> None:
    path = sidecar_path(video_path)
    path.write_text(json.dumps({"frames": frames}, indent=2), encoding="utf-8")


def list_commenters(video_path: Path) -> set[str]:
    """Every distinct comment author across all of this video's frames —
    used by filter_sidebar.py's "Commented By" filter category (added
    2026-07-20). A frame that only has the legacy single `"note"` string
    (pre-dates per-comment authorship) contributes nothing here — there's
    no author recorded for it, unlike PlayerWidget._migrate_comments'
    display-only synthesis of a one-item comments list."""
    frames = load(video_path).get("frames", {})
    authors = set()
    for entry in frames.values():
        for comment in entry.get("comments", []):
            author = comment.get("author")
            if author:
                authors.add(author)
    return authors

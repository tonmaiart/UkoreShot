from __future__ import annotations

from pathlib import Path


def resolve_video_root(api, project_id: str, repo_id: str) -> Path:
    """Absolute folder this repo's playblast videos live in — UkoreHub's
    own per-machine cache_dir (gitignored), keyed by project_id/repo_id, so
    the video library is local to each machine and never travels through
    git/repo sync (see UkorePlayblast's matching
    PublishApi.repo_paths.find_cache_dir()-based _resolve_video_root, which
    writes into this exact same folder). Created on first resolution so
    callers can rely on it already existing."""
    video_root = api.cache_dir / "ukore_shot" / project_id / repo_id
    video_root.mkdir(parents=True, exist_ok=True)
    return video_root


def resolve_export_dir(api, project_id: str, repo_id: str) -> Path:
    """Absolute folder Get Video / Get Video - Commented write their
    generated .mp4s into — deliberately a *sibling* of "ukore_shot" above,
    not nested under it, so it's never picked up by
    video_library_page.py's `_video_root.rglob("*")` library scan (an
    export showing up as its own library row would be wrong). Same
    per-machine cache_dir as resolve_video_root, so these exports are
    local-only and never travel through git/repo sync or this plugin's own
    cloud-sync code (core/share_sync.py never touches this folder) —
    confirmed with the user that Get Video output must never be synced.
    Created on first resolution, same as resolve_video_root."""
    export_dir = api.cache_dir / "ukore_shot_exports" / project_id / repo_id
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir

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

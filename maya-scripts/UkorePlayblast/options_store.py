"""Per-repo playblast option storage — read/written entirely from inside
Maya (function.py's publish_playblast, options_dialog.py's "Playblast
Options..." dialog) since this tool has no UkoreHub desktop UI of its own
(confirmed with the user 2026-07-19 — see plugin.py).

Storage: <active repo's own local clone>/.ukorehub/ukore_playblast.json,
committed to that repo's own git history like PublishApi's
PublishValidation/ scripts — these are team-shared playblast defaults
(resolution/format/variations) every artist on the repo should see, not a
personal/per-machine preference. Moved out of the old shared
data/plugins/core/ukore_playblast.json blob (every studio repo's options in
one cloud-synced file, keyed by "<project_id>:<repo_id>") since that file
requires Google Cloud Storage access this Maya process doesn't have (no
google-cloud-storage in mayapy's site-packages) — see
_migrate_from_shared_store below for how an existing repo's saved options
are carried forward on first access. Constructs the PluginConfigStore
straight off disk, same pattern every Maya-side module in this codebase
uses (Maya's Python has no PluginAPI instance) — PublishApi.repo_paths and
PublishApi.tickets are the established examples."""

from __future__ import annotations

import re

from PublishApi import repo_paths

_OPTIONS_KEY = "repo_options"
_VARIATIONS_KEY = "repo_variations"

# Built-in variation choices always offered regardless of repo — a repo
# can add more on top via add_variation, confirmed with the user
# 2026-07-20 that custom variations are scoped per-repo (get_variations
# below), same as every other per-repo choice this store already holds.
BUILTIN_VARIATIONS = ["layout", "blocking", "spline"]

DEFAULT_OPTIONS = {
    "resolution_mode": "render_settings",  # "render_settings" | "custom"
    "width": 1920,
    "height": 1080,
    # "qt"/"H.264" — reverted 2026-08-20 (was "avi"/"" from 2026-07-19,
    # after a real "Unable to create a movie file" failure — older Maya on
    # Windows had no QuickTime backend at all for "qt"). Confirmed with the
    # user that video-to-image-sequence splitting is now UkoreShot's own
    # desktop-side job (core/video_sequence.py, ffmpeg-based, lazy — only on
    # Comment/Mark as Share) rather than something Maya writes alongside the
    # video, so Maya just needs a normal playable H.264 file again — no
    # reason to stay on the fallback-workaround "avi" format for that. If
    # this ever regresses on a given Maya/Windows combo, "avi"/"" is the
    # known-safe fallback (see git history 2026-07-19 for that incident).
    # Applies to "video" output_mode only — "image" format used to be a
    # third choice here too, but that produced a full-timeline image
    # sequence via Maya's own per-frame numbering, not the deliberate
    # single-current-frame capture the user asked for 2026-07-20; that's
    # now output_mode/image_format below instead, a separate axis.
    "format": "qt",
    "compression": "H.264",
    "quality": 80,
    "percent": 80,
    "frame_range_mode": "current_timeline",  # "current_timeline" | "custom"
    "start_frame": 1,
    "end_frame": 100,
    "camera": "",  # empty = active viewport camera
    "sound": True,
    "show_ornaments": False,
    # "video" | "current_frame_image" — added 2026-07-20 alongside the
    # flat SEQ_Shot_variation_index_version naming convention (see
    # maya-scripts/README.md's "Flat naming convention" section and
    # function.py's _resolve_filename_stem): "current_frame_image" captures
    # exactly the frame Maya's playhead is on right now, not a range, and
    # adds a new index onto whichever version for this shot/variation
    # already exists instead of starting a fresh one — the "video" mode is
    # unchanged from before.
    "output_mode": "video",
    "image_format": "png",
    # Which BUILTIN_VARIATIONS/repo-custom entry the naming convention's
    # "variation" token comes from for this repo's next playblast.
    "variation": "layout",
}


def _repo_key(project_id, repo_id):
    return "{}:{}".format(project_id, repo_id)


def _sanitize_token(value: str) -> str:
    """Mirrors function.py's own _sanitize_token exactly (small enough,
    and used by different concerns — storage vs. filename-building — that
    a shared module would be more ceremony than the two-line function
    warrants; same reasoning _repo_key above is already duplicated
    independently in both files for). Strips anything that isn't a
    letter/digit so a custom variation can never contain the "_" the flat
    naming convention's stem is split on."""
    return re.sub(r"[^A-Za-z0-9]", "", value) or "x"


def _repo_store():
    """The active repo's own .ukorehub/ukore_playblast.json — None if
    there's no active repo."""
    from core.extensibility.config_store import PluginConfigStore

    _project, _repo, repo_path = repo_paths.get_active_repo()
    if repo_path is None:
        return None
    return PluginConfigStore(repo_path / ".ukorehub" / "ukore_playblast.json")


def _migrate_from_shared_store(key: str, project_id, repo_id):
    """One-time carry-forward for a repo that already had options/variations
    saved in the old shared data/plugins/core/ukore_playblast.json blob
    (pre-migration to each repo's own .ukorehub/ folder). Returns None (for
    _OPTIONS_KEY) or [] (for _VARIATIONS_KEY) if nothing was saved there."""
    from core.extensibility.config_store import PluginConfigStore

    root = repo_paths.find_ukorehub_root()
    shared_store = PluginConfigStore(root / "data" / "plugins" / "core" / "ukore_playblast.json")
    return shared_store.get(key, {}).get(_repo_key(project_id, repo_id))


def get_options(project_id, repo_id):
    """DEFAULT_OPTIONS merged with whatever's saved for this repo — every
    field always present, so callers never need a .get() fallback."""
    store = _repo_store()
    if store is None:
        return dict(DEFAULT_OPTIONS)

    saved = store.get(_OPTIONS_KEY)
    if saved is None:
        saved = _migrate_from_shared_store(_OPTIONS_KEY, project_id, repo_id) or {}
        store.set(_OPTIONS_KEY, saved)

    options = dict(DEFAULT_OPTIONS)
    options.update(saved)
    return options


def set_options(project_id, repo_id, options):
    store = _repo_store()
    if store is None:
        return
    store.set(_OPTIONS_KEY, options)


def get_variations(project_id, repo_id):
    """BUILTIN_VARIATIONS plus this repo's own custom additions (order
    preserved, no duplicates) — safe to call with project_id/repo_id both
    None (no active repo), returning just the builtins."""
    saved = []
    store = _repo_store()
    if store is not None:
        saved = store.get(_VARIATIONS_KEY)
        if saved is None:
            saved = _migrate_from_shared_store(_VARIATIONS_KEY, project_id, repo_id) or []
            store.set(_VARIATIONS_KEY, saved)

    result = list(BUILTIN_VARIATIONS)
    for variation in saved:
        if variation not in result:
            result.append(variation)
    return result


def add_variation(project_id, repo_id, variation) -> str:
    """Sanitizes and persists a new custom variation for this repo,
    returning the sanitized value actually saved so the caller (the
    options dialog) can select exactly that. A no-op (beyond sanitizing,
    and beyond the no-active-repo case, which just returns without saving)
    if it's already a builtin or already saved for this repo."""
    sanitized = _sanitize_token(variation)
    store = _repo_store()
    if store is None:
        return sanitized
    repo_variations = list(store.get(_VARIATIONS_KEY) or [])
    if sanitized not in BUILTIN_VARIATIONS and sanitized not in repo_variations:
        repo_variations.append(sanitized)
        store.set(_VARIATIONS_KEY, repo_variations)
    return sanitized

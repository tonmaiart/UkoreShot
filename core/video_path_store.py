from __future__ import annotations

from pathlib import Path

from core.exceptions import NotFoundError

_SELECTION_KEY = "repo_video_custom_path"


def _repo_key(project_id: str, repo_id: str) -> str:
    return f"{project_id}:{repo_id}"


def get_custom_paths(api, project_id: str, repo_id: str) -> list[dict]:
    """This repo's own declared Custom Paths, read directly off
    project_editor's shared PluginConfigStore — the "convention, not
    import" pattern plugins/README.md documents (see
    plugins/core/project_editor/README.md's "Reading pipeline data from
    another plugin"), same approach every other tool plugin's own Repo
    Studio Setting tab already uses (e.g. RigPublisherSettingsPage, just
    reading "custom_paths" instead of "pipeline_inputs")."""
    store = api.plugin_config_store("project_editor", shared=True)
    entry = store.get("projects", {}).get(project_id, {}).get("repos", {}).get(repo_id, {})
    return entry.get("custom_paths", [])


def get_selected_custom_path_id(api, project_id: str, repo_id: str) -> str | None:
    """The explicitly-chosen custom_path_id for this repo, or None if a
    studio admin hasn't picked one yet (see resolve_video_root for the
    "auto-use if there's only one" fallback that applies in that case)."""
    store = api.plugin_config_store("ukore_shot", shared=True)
    return store.get(_SELECTION_KEY, {}).get(_repo_key(project_id, repo_id))


def set_selected_custom_path_id(api, project_id: str, repo_id: str, custom_path_id: str | None) -> None:
    store = api.plugin_config_store("ukore_shot", shared=True)
    selections = store.get(_SELECTION_KEY, {})
    key = _repo_key(project_id, repo_id)
    if custom_path_id is None:
        selections.pop(key, None)
    else:
        selections[key] = custom_path_id
    store.set(_SELECTION_KEY, selections)


def resolve_video_root(api, project_id: str, repo_id: str) -> Path | None:
    """Absolute folder this repo's playblast videos live in, per whatever
    Custom Path Repository Setting > UkoreShot has chosen (see
    repo_video_settings_page.py). Resolution order mirrors
    PublishApi.repo_paths.get_publish_root's own convention: an explicit
    choice wins, else the repo's only declared Custom Path is used
    automatically (no ambiguity to resolve), else there's nothing to
    resolve. Returns None if the repo has no Custom Paths declared at all,
    the chosen one no longer exists, or the repo record itself is gone."""
    custom_paths = get_custom_paths(api, project_id, repo_id)
    if not custom_paths:
        return None

    custom_path_id = get_selected_custom_path_id(api, project_id, repo_id)
    if custom_path_id is None:
        if len(custom_paths) != 1:
            return None
        custom_path = custom_paths[0]
    else:
        custom_path = next((cp for cp in custom_paths if cp.get("id") == custom_path_id), None)
        if custom_path is None:
            return None

    try:
        repo = api.metadata.get_repo(project_id, repo_id)
    except NotFoundError:
        return None
    # custom_path["path"] is raw, unsanitized user input (see
    # developer/bug-history/2026-07-20-playblast-custom-path-leading-slash.md) — a
    # leading "/" or "\" makes pathlib treat it as anchored to the current
    # drive, discarding workspace_root/repo.local_path entirely.
    return Path(api.local_config.workspace_root) / repo.local_path / custom_path["path"].lstrip("/\\")

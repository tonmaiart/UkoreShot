from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from plugin_api import CATEGORY_REPO, SectionSpec, SettingsTabSpec

# This plugin lives in its own separate git repo, cloned into
# cache/plugins/UkoreShot/ rather than under the main app's plugins/
# package — so its sibling files can't import each other via a
# plugins.<root>.UkoreShot.* dotted path the way a bundled plugin does.
# Register this folder as a real package under a private synthetic name
# before importing anything from it, so every sibling file's own
# `from ukoreshot_plugin.core import ...`-style import resolves normally.
_PLUGIN_ROOT = Path(__file__).resolve().parent
_PACKAGE_NAME = "ukoreshot_plugin"


def _load_as_package() -> None:
    if _PACKAGE_NAME in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME,
        _PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE_NAME] = module
    spec.loader.exec_module(module)


_load_as_package()

from ukoreshot_plugin.interface.repo_video_settings_page import RepoVideoSettingsPage  # noqa: E402
from ukoreshot_plugin.interface.video_library_page import UkoreShotPage  # noqa: E402

PLUGIN_ID = "ukore_shot"

# Maya-side playblast tool (maya-scripts/UkorePlayblast/), merged into this
# plugin 2026-08-20 — previously its own separate "ukore_playblast" plugin.
# The bridge's tool_id MUST equal a plugin id maya_launcher's own
# api.plugin_catalog actually discovers this launch (a real manifest.json
# id) — _prepare_env_and_plugins() gates every contribution/launch_hook on
# tool_id being both in repo.required_plugin_ids AND in plugin_catalog, not
# just present in the bridge JSON (see maya_launcher/README.md). A
# made-up id like the old "ukore_playblast" no longer matches any real
# plugin now that plugin merged into this one, so it silently never
# activates — must be this plugin's own manifest id, same reason
# MayaToolkit's TOOL_ID == "maya_toolkit" and UkoreReferenceEditor's
# TOOL_ID == "ukore_reference_editor" both equal their own manifest ids.
_MAYA_TOOL_ID = PLUGIN_ID
_MAYA_TOOL_LABEL = "UkoreShot"
_MAYA_ENV_BRIDGE_PLUGIN_ID = "maya_launcher_env_bridge"
_ANY_VERSION = "*"
# This plugin's own images/ folder, not the shared data/icons/ every other
# plugin's SectionSpec.icon_path points at (see images/README.md's own
# note on this deliberate exception).
_ICON_PATH = Path(__file__).resolve().parent / "images" / "icons8-video-50.png"


def register(api) -> None:
    # A normal (non-persistent) section — main_window.py's
    # _apply_plugin_visibility already hides this tab for any repo whose
    # Repo.required_plugin_ids doesn't include "ukore_shot" (Settings >
    # Repo > Requirements & Plugins, opt-in — the same required_plugin_ids
    # list gates both a repo_internal/ plugin and a cache/plugins/ repo
    # plugin like this one), the exact "appears only in the Sidebar of
    # opted-in repos" behavior asked for — no extra plumbing needed, see
    # launcher.py's section_key_to_plugin_id diffing.
    #
    # No more `wire`/UICommandService hand-off (removed 2026-08-20) — Edit
    # Comment used to open the separate BananaSketch plugin via
    # host.navigate_and_focus("banana_sketch", ...); it now opens this
    # plugin's own in-house CommentEditor directly (see
    # video_library_page.py's _on_edit_comment_clicked/comment_editor.py),
    # so UkoreShotPage no longer needs a UICommandService at all. Still
    # constructed once here rather than inside page_factory's lambda — this
    # page holds real session state (video list, selected entry, the
    # background ThumbnailLoader) that must survive a tab switch, not be
    # rebuilt from scratch every time the Sidebar shows this section again.
    page = UkoreShotPage(api=api)
    api.register_section(
        SectionSpec(
            key=PLUGIN_ID,
            label="UkoreShot",
            order=50,
            page_factory=lambda: page,
            icon_path=_ICON_PATH,
        )
    )
    api.register_settings_tab(
        SettingsTabSpec(
            key=PLUGIN_ID,
            label="UkoreShot",
            order=125,
            page_factory=lambda: RepoVideoSettingsPage(api=api),
            on_activated=lambda page: page.refresh(),
            category=CATEGORY_REPO,
        )
    )

    # Contributes maya-scripts/UkorePlayblast/ to Maya's PYTHONPATH via the
    # shared maya_launcher_env_bridge, same convention every other Maya tool
    # plugin uses (core.* importability itself already comes from
    # PublishApi's own contribution, which also adds api.app_root to
    # PYTHONPATH — this plugin requires "publish_api", see manifest.json).
    bridge = api.project_plugin_config_store(_MAYA_ENV_BRIDGE_PLUGIN_ID)
    if bridge is not None:
        tool_root = Path(__file__).resolve().parent
        contributions = bridge.get("contributions", {})
        contributions[_MAYA_TOOL_ID] = {"PYTHONPATH": {_ANY_VERSION: [str(tool_root / "maya-scripts")]}}
        bridge.set("contributions", contributions)
        labels = bridge.get("labels", {})
        labels[_MAYA_TOOL_ID] = _MAYA_TOOL_LABEL
        bridge.set("labels", labels)

        # maya-scripts/UkorePlayblast/__init__.py registers this tool's own
        # "Playblast"/"Playblast Options..." items directly into ukore_menu
        # (no longer wired through MayaToolkit) — must be imported every
        # Maya session for that module-level registration to run at all, see
        # UkoreMenu's own README's "auto-import ตอน Maya เปิดไฟล์" requirement.
        # order must stay below UkoreMenu's own (99) so registration runs
        # before UkoreMenu's rebuild_menu().
        hooks = bridge.get("launch_hooks", {})
        hooks[_MAYA_TOOL_ID] = {
            "order": 10,
            "pre_open_mel": 'python("try:\\n    import UkorePlayblast\\nexcept ImportError:\\n    pass");',
        }
        bridge.set("launch_hooks", hooks)

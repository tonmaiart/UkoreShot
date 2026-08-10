from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from interface.section_registry import SectionHost, SectionSpec
from interface.settings_tab_registry import CATEGORY_REPO, SettingsTabSpec

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
# This plugin's own images/ folder, not the shared data/icons/ every other
# plugin's SectionSpec.icon_path points at (see images/README.md's own
# note on this deliberate exception).
_ICON_PATH = Path(__file__).resolve().parent / "images" / "icons8-video-50.png"


def _wire(page: UkoreShotPage, host: SectionHost) -> None:
    # UkoreShot's own Edit Comment button needs to open BananaSketch
    # (host.navigate_and_focus("banana_sketch", video_path)) instead of an
    # in-app dialog now that the draw/comment editor lives in that
    # separate plugin (moved 2026-08-08) — see
    # video_library_page.py's _on_edit_comment_clicked.
    page.set_host(host)


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
    # page is constructed once here (not inside page_factory's lambda) so
    # _wire receives the exact same instance the Sidebar ends up showing —
    # mirrors plugins/core/submit/plugin.py's own _wire pattern, the
    # documented example this cross-plugin wiring follows.
    page = UkoreShotPage(api=api)
    api.register_section(
        SectionSpec(
            key=PLUGIN_ID,
            label="UkoreShot",
            order=50,
            page_factory=lambda: page,
            icon_path=_ICON_PATH,
            wire=_wire,
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

# UkoreShot

Per-repo playblast video library + review. This is its own standalone git
repository (`github.com/tonmaiart/UkoreShot`), cloned into the main
UkoreHub app's `cache/plugins/UkoreShot/` on any machine that needs it —
moved out of that app's own repo (previously bundled at
`plugins/repo_internal/UkoreShot/`, before that `plugins/core/UkoreShot/`)
on 2026-08-08, the same "repo plugin" shape `mGear`/`StudioLibrary` already
use (see the main app's `plugins/README.md`'s `cache/plugins/` section).
Nothing about the feature itself changed in that move — only where its
source lives and how its own files import each other (see "Plugin entry
point" below).

A normal (non-persistent) `SectionSpec` sidebar tab in the host app —
visible only for repos that opted in under Settings > Repo > Requirements
& Plugins' plugin list (`Repo.required_plugin_ids`, keyed by this plugin's
`manifest.json` id `ukore_shot` — the same list gates both a
`repo_internal/` plugin and a `cache/plugins/` repo plugin like this one,
so no data-model change was needed for the move), the same opt-in
mechanism every other opt-in plugin's sidebar tab already uses (see the
host app's `interface/main_window.py`'s `_apply_plugin_visibility`).
Companion to the host app's `plugins/repo_internal/UkorePlayblast/`, which
writes the video files this plugin's library lists — that plugin stayed
put, still bundled in the main app.

## Structure

Split into subfolders by concern on 2026-07-21, specifically so a session
can **read only the subfolder(s) a given task actually touches** instead
of the whole plugin — this folder had grown to over a dozen flat files,
most of them irrelevant to any one task:

- [`core/`](core/README.md) — non-UI logic: video-root path resolution,
  comment persistence, playblast filename parsing. No PySide6 imports.
- [`interface/`](interface/README.md) — every PySide6 widget/page/dialog
  this plugin has.
- [`images/`](images/README.md) — this plugin's own icon files (not the
  shared `data/icons/` every other plugin uses — see that README for why).
- [`bug-history/`](bug-history/README.md) — bugs fixed specifically within
  this plugin's own code, same format as the repo-root `developer/bug-history/`,
  going forward from 2026-07-21.
- `manifest.json` / `plugin.py` / `__init__.py` — plugin entry point,
  stay at this top level: the host app's plugin loader
  (`core/extensibility/loader.py`'s `_load_one`) looks for both
  `manifest.json` and `manifest.json`'s `entry_point` directly inside a
  plugin's own top-level directory, not in a subfolder, and only ever
  imports `plugin.py` itself that way.

**Plugin entry point / how sibling imports work here:** since this repo
isn't nested under the host app's own `plugins/` package, `plugin.py`
can't reach its own siblings via a `plugins.<root>.UkoreShot.*` dotted
import the way a bundled plugin does. Its first lines instead register
this folder as a real Python package under a private synthetic name
(`ukoreshot_plugin`) via `importlib.util.spec_from_file_location(...,
submodule_search_locations=[...])`, *before* importing anything from
`interface/`/`core/` — every other file in `core/`/`interface/` then
imports its own siblings normally as `from ukoreshot_plugin.core import
...` / `from ukoreshot_plugin.interface... import ...`. See `plugin.py`
itself for the exact bootstrap. The two files with a bare `from core...`
import (`core/comment_store.py`, `interface/draw_overlay.py`) are
unaffected by any of this — see the naming-collision note below.

**Before touching a file in one of the four subfolders above, read that
subfolder's own README first** — the same "read the local README before
opening individual files" rule root `CLAUDE.md` already applies to every
top-level folder in this repo, just one level deeper here. Concretely: a
task about where videos are found on disk or how comments persist only
needs `core/`; a task about a button, dialog, or layout only needs
`interface/`; don't open a sibling subfolder "just in case" unless the
task genuinely crosses the boundary (same discipline the `ukorehub-plugin`
skill already asks for between *different* plugins — this applies it one
level down, *within* this one plugin).

**Naming collision to know about:** two different files in this plugin
import a bare `from core...` — `core/comment_store.py`'s
`from core.store import LocalConfigStore` and
`interface/draw_overlay.py`'s `from core.extensibility import debug_log`.
Both mean the app's own **top-level** `core/` package
(`C:\Tonmai\UkoreHub\core\`), never this plugin's own `core/` subfolder —
they're absolute imports, resolved from the repo root regardless of where
the importing file lives, so there's no actual ambiguity at runtime; it's
only confusing to a human skimming the two folders side by side. See
`core/README.md`'s own naming note for more.

For the `ukoreshot` skill (project-scoped, for working specifically in
this plugin), see the host app's `.claude/skills/ukoreshot/SKILL.md` —
that skill lives in the main UkoreHub repo, not in this one, since it's
only relevant when this plugin is checked out inside that app's own
`cache/plugins/UkoreShot/` working copy.

## Where the video library folder comes from

UkoreShot does **not** own its own free-text folder setting — a studio
admin picks one of the active repo's own declared Custom Paths under
Repository Setting > UkoreShot instead. See `core/README.md`'s
`video_path_store.py` entry for the exact resolution order and how it
ties into `plugins/core/project_editor/`'s Custom Paths and
`plugins/repo_internal/UkorePlayblast/`'s output folder.

## Send to Discord

Added 2026-08-08: a `send_discord_button` in the library's inline video
player (`interface/player_widget.py`) posts whichever video is loaded to a
Discord channel via a bot, for quick review outside the app. Channel ID is
per-repo/studio-shared, the bot token is per-machine (OS keyring) — both
configured under Repository Setting > UkoreShot. See
`core/README.md`'s `discord_client.py`/`discord_token_store.py` entries and
`interface/README.md`'s `discord_send_worker.py`/`repo_video_settings_page.py`
entries for the full mechanics.

**Working here:** stay inside this plugin folder (respecting the
subfolder-scoping rule above) unless the change needs a new top-level
`core/` primitive, or touches `plugins/core/project_editor/`'s Custom
Paths data shape (read-only, via the convention in `core/README.md`) or
`plugins/repo_internal/UkorePlayblast/`'s output (read-only, both plugins just
happen to agree on the same resolved folder — see that plugin's own
README for the Maya-side half of this feature).

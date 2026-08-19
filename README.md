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
put, still bundled in the main app. As of 2026-08-08, this plugin is
view-only: the whole draw/comment editor (drawing on a frame, per-frame
comments, everything that used to live in `EditVideoDialog`) was
extracted into its own separate plugin, `cache/plugins/BananaSketch/` —
"Edit Comment" now opens that plugin instead of an in-app dialog (see
`interface/README.md`'s `video_library_page.py` entry).

## Structure

Split into subfolders by concern on 2026-07-21, specifically so a session
can **read only the subfolder(s) a given task actually touches** instead
of the whole plugin — this folder had grown to over a dozen flat files,
most of them irrelevant to any one task:

- [`core/`](core/README.md) — non-UI logic: video-root path resolution,
  playblast filename parsing, the Discord-send API client. No PySide6
  imports.
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
itself for the exact bootstrap. `core/video_path_store.py`'s bare
`from core...` import is unaffected by any of this — see the naming note
below.

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

**Naming note:** `core/video_path_store.py` imports a bare `from
core.exceptions import NotFoundError` — this always means the app's own
**top-level** `core/` package (`C:\Tonmai\UkoreHub\core\`), never this
plugin's own `core/` subfolder — it's an absolute import, resolved from
the repo root regardless of where the importing file lives, so there's no
actual ambiguity at runtime; it's only confusing to a human skimming the
two folders side by side. See `core/README.md`'s own naming note for more.

For the `ukoreshot` skill (project-scoped, for working specifically in
this plugin), see the host app's `.claude/skills/ukoreshot/SKILL.md` —
that skill lives in the main UkoreHub repo, not in this one, since it's
only relevant when this plugin is checked out inside that app's own
`cache/plugins/UkoreShot/` working copy.

## Where the video library folder comes from

The playblast video library is a fixed per-machine folder under UkoreHub's
own gitignored `cache/` directory (`api.cache_dir / "ukore_shot" /
<project_id> / <repo_id>`) — not something a studio admin configures, and
not inside the repo checkout at all, so it's never synced via git/repo
sync and never shared across machines. See `core/README.md`'s
`video_path_store.py` entry for the exact resolution, and
`cache/plugins/UkorePlayblast/`'s README for how the Maya-side writer
resolves the same folder independently.

## Send to Discord

Added 2026-08-08: a `send_discord_button` in the library's inline video
player (`interface/player_widget.py`) posts whichever video is loaded into
a **Discord forum post** for quick review outside the app — specifically
the post whose title matches the video's own shot code (e.g. `KBA030`,
from `core/video_naming.py`'s parse), reusing one if it already exists or
creating it otherwise (`core/discord_client.py`'s
`find_or_create_forum_post`). Both Forum Channel ID and Bot Token are
per-repo and studio-shared (git-tracked), configured under Repository
Setting > UkoreShot — **the Bot Token is plain text in this repo's git
history**, a deliberate tradeoff confirmed with the user so every machine
gets it automatically via git instead of each one needing the token
entered separately into an OS keyring (an earlier revision's approach);
only use a bot you're fine with the whole studio effectively controlling.
A video over the configured Max Upload Size (default 10MB, Discord's own
un-boosted cap) is compressed with `ffmpeg` first
(`core/video_compress.py`'s `compress_to_fit`, called from
`interface/discord_send_worker.py`) — requires `ffmpeg` installed on
whichever machine clicks the button; see Repository Setting > UkoreShot's
Max Upload Size / ffmpeg Path fields.

Editing a post's description/title/thumbnail after it's created
(`/setdesc`, `/settitle`, `/thumbnail`) is **not** handled by UkoreHub at
all — that's `Jacobot` (`cache/plugins/Jacobot/`, a separate always-on bot
service, own README explains why a persistent server is required for
Discord slash commands in a way a desktop app can't provide). UkoreHub's
only job is creating the post with a bot-authored placeholder starter
message in the first place, which is what makes those commands able to
edit it afterward (Discord only lets a message's own author edit it).

See `core/README.md`'s `discord_client.py` entry and
`interface/README.md`'s `discord_send_worker.py`/`repo_video_settings_page.py`
entries for the full mechanics.

**Working here:** stay inside this plugin folder (respecting the
subfolder-scoping rule above) unless the change needs a new top-level
`core/` primitive, or touches `cache/plugins/UkorePlayblast/`'s output
(read-only, both plugins just happen to agree on the same
`cache_dir`-derived folder — see that plugin's own README for the
Maya-side half of this feature).

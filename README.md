# UkoreShot

Per-repo playblast video library + review, plus the Maya-side playblast
tool that writes the videos it lists. This is its own standalone git
repository (`github.com/tonmaiart/UkoreShot`), cloned into the main
UkoreHub app's `cache/plugins/UkoreShot/` on any machine that needs it.
**Standalone tool — no Discord integration** (removed 2026-08-21; this
plugin no longer talks to any external chat service).

A normal (non-persistent) `SectionSpec` sidebar tab in the host app —
visible only for repos that opted in under Settings > Repo > Requirements
& Plugins' plugin list (`Repo.required_plugin_ids`, keyed by this plugin's
`manifest.json` id `ukore_shot`), the same opt-in mechanism every other
opt-in plugin's sidebar tab already uses.

## Structure

Split into subfolders by concern (previously each had its own `README.md`
— consolidated into this single top-level file 2026-08-21 for
cleanliness; read the file names/docstrings inside each subfolder for
further detail):

- `core/` — non-UI logic, no PySide6 imports at all. `video_path_store.py`
  resolves both the (fixed, per-machine, gitignored, under UkoreHub's own
  `cache_dir`) video library root and a separate export folder for Get
  Video/Get Video - Commented (see "Get Video" below). `video_naming.py`
  parses UkorePlayblast's flat `SEQ_ShotCode_Variation_index_version.ext`
  filenames. `video_sequence.py` lazily extracts a video into a numbered
  PNG sequence + optional audio track via ffmpeg (`ensure_sequence`), and
  the reverse direction (`encode_sequence_to_video`) for exporting a
  composited sequence back to an `.mp4`. `comment_store.py` is per-video
  comment/drawing/share metadata (`<sequence_dir>/comments.json`).
  `share_sync.py` is the background R2 push/pull for a shared video, via
  `api.cloud_sync`. `video_compress.py` shells out to ffmpeg to transcode
  a video under a byte cap (`compress_to_fit`), auto-downloading a win64
  ffmpeg build into `cache_dir` on first use if none is configured/on
  PATH, and holds the per-machine ffmpeg-path setting
  (`get_ffmpeg_path`/`set_ffmpeg_path`).
- `interface/` — every PySide6 widget/page/dialog. Rebuilt 2026-08-20
  against two user-authored Qt Designer `.ui` files
  (`UkoreShotPage.ui`, `CommentEditor.ui`), loaded at runtime via
  `QUiLoader` — the `.ui` supplies layout/widget identity, the Python
  wires behavior. `video_library_page.py`'s `UkoreShotPage` is the
  section's top-level widget (search/sort/table + an inline `PlayerWidget`
  viewer + Comment/Mark as Share/Get Video/Get Video - Commented/Delete).
  `tableWidget_playblast_library` is multi-selectable (`ExtendedSelection`,
  2026-08-21) — `pushButton_delete_playblast` deletes every selected row's
  local video file + `sequence_dir` (comments.json included), confirmed
  with a dialog first; deleting a shared entry only ever removes the local
  copy (R2JsonSync has no delete-blob operation, so the cloud copy and its
  share code both keep working from any other machine — this is
  deliberate, not a TODO). Mark as Share is disabled once an entry is
  already shared. Shift+A/Shift+D jump to the previous/next commented
  keyframe here too, same as `CommentEditor`'s own shortcut.
  `pushButton_sort_oldest` was removed from `UkoreShotPage.ui` 2026-08-21
  (name-ascending/newest-first are the only sort modes left).
  `player_widget.py`'s `PlayerWidget` plays either a real video file
  (`QMediaPlayer`) or an already-extracted image sequence
  (`sequence_player.py`'s `SequencePlayer`, frame-accurate, used by
  `CommentEditor` always). `draw_overlay.py`'s `DrawOverlay` is the
  freehand brush/eraser/select drawing canvas (`show_edit_tools=True`
  only). `comment_editor.py`'s `CommentEditor` is the draw/comment dialog,
  operating on an already-extracted `sequence_dir`, wired onto
  `CommentEditor.ui`'s own transport/comment-nav/undo-redo-clear buttons
  (rewired 2026-08-21 — previously duplicate widgets were built in code
  instead of using the `.ui`'s own). `thumbnail_loader.py` grabs a video's
  first decodable frame. `repo_video_settings_page.py` is the
  `CATEGORY_REPO` Settings tab (just the ffmpeg Path field, since Discord
  removal).
- `images/` — this plugin's own icon files (a deliberate exception to the
  rest of the codebase's `data/icons/` convention — resolved via
  `_ICONS_DIR = Path(__file__).resolve().parents[1] / "images"` from
  `player_widget.py`/`comment_editor.py`). Don't open the PNGs
  speculatively — check the `_..._ICON_PATH` constants in those two files
  for which icon a given button uses.
- `bug-history/` — bugs fixed specifically within this plugin's own code
  (same format as the host app's own bug-history), going forward from
  2026-07-21.
- `maya-scripts/` — the Maya-side playblast tool (`UkorePlayblast/`
  package), merged in 2026-08-20 from its own former separate plugin. A
  completely separate Python environment (Maya's own `mayapy`, not this
  desktop app) — nothing here imports from `core/`/`interface/`, and vice
  versa; where both sides need to agree on something (the video-root
  folder, the naming convention) it's duplicated deliberately rather than
  shared. Registers "Ukore Shot Playblast" (General category) and
  "Playblast Options..." (Anim category) into `ukore_menu`'s central
  "Ukore Tools" registry. `function.py`'s `publish_playblast()` also
  auto-disables Film Gate on the capturing camera for the duration of the
  capture (restored afterward either way).
- `manifest.json` / `plugin.py` / `__init__.py` — plugin entry point, stay
  at this top level: the host app's plugin loader looks for both directly
  inside a plugin's own top-level directory.

**Plugin entry point / how sibling imports work here:** since this repo
isn't nested under the host app's own `plugins/` package, `plugin.py`
registers this folder as a real Python package under a private synthetic
name (`ukoreshot_plugin`) via `importlib.util.spec_from_file_location(...,
submodule_search_locations=[...])` before importing anything from
`interface/`/`core/` — every other file in `core/`/`interface/` then
imports its own siblings as `from ukoreshot_plugin.core import ...` /
`from ukoreshot_plugin.interface... import ...`. See `plugin.py` itself
for the exact bootstrap.

**Naming note:** this plugin's own `core/` subfolder has no direct
`from core.xxx import yyy` imports of the *host app's* top-level `core/`
package anymore — `comment_store.py` used to (`from core.store import
LocalConfigStore`), found broken 2026-08-21 after the host app's own
`core/` layout changed underneath it, and fixed to thread the documented
`api` parameter through instead. Nothing in `core/`/`interface/` should
add a bare `from core...` import again — go through `plugin_api` or
thread `api` through instead, since the host app's own `core/` layout
isn't a stable contract this plugin can assume across app versions.

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
sync and never shared across machines. `maya-scripts/UkorePlayblast/function.py`'s
`_resolve_video_root` resolves the exact same folder independently
(duplicated, not imported — `mayapy` has no `PluginAPI` instance).

## Sharing (image sequences, share codes, cloud sync)

A video's `.mp4` is never itself synced anywhere — what travels to the
cloud (and what `comment_editor.py`'s viewer actually draws on) is a
lazily-extracted PNG sequence (`core/video_sequence.py`, ffmpeg-based,
`<video_root>/<stem>/`) plus its `comments.json` (`core/comment_store.py`).
"Lazily" is load-bearing here: browsing the library never triggers an
extraction; it only happens the first time Comment, Mark as Share, or Get
Video - Commented is clicked for a given video. That same lazy extraction
also pulls the video's audio track (if it has one) into `<stem>.audio.m4a`
alongside the frames, which `interface/sequence_player.py` plays back in
sync with the frame sequence.

"Mark as Share" (`interface/video_library_page.py`'s
`pushButton_mark_as_share`) uploads that sequence + `comments.json` to
Cloudflare R2 via `api.cloud_sync` (`core/share_sync.py`'s
`ShareUploadWorker`), then generates and persists a
`{ShotCode}_v{version}_{4 hex chars}` code (`comment_store.generate_share_code`)
and pushes a small pointer blob (`share_sync.push_pointer`) that makes the
code resolvable. "Copy Share Code" copies that code as plain text.

Pasting that same code into `lineEdit_search_bar` and pressing Enter on a
*different* machine pulls the sequence + `comments.json` back down
automatically (`share_sync.PullByCodeWorker`) — the video shows up in the
table from its pulled sequence alone, with no local `.mp4` ever existing
for it on that machine. Saving a comment in `comment_editor.py` on a video
that's already shared also pushes just the updated `comments.json`
incrementally (`share_sync.CommentSyncWorker`) rather than requiring a
fresh Mark as Share for every comment.

## Get Video / Get Video - Commented

Added 2026-08-21: `pushButton_get_video`/`pushButton_get_video_commented`
in `video_library_page.py` each generate a fresh, hard-capped-at-20MB
`.mp4` on click, then reveal+select it in Windows Explorer
(`explorer /select,`). Get Video just compresses the selected video's own
source file (`core/video_compress.py`'s `compress_to_fit`, unchanged if
already under the cap); Get Video - Commented composites each extracted
frame's saved drawing (`draw_overlay.py`'s `paint_stroke_points`) onto its
own image first, then encodes that sequence back into a video
(`core/video_sequence.py`'s `encode_sequence_to_video`) before the same
compression pass. Both write into a dedicated per-repo export folder
(`core/video_path_store.py`'s `resolve_export_dir`, a sibling of the video
library root, never scanned by it) — **local-only, overwritten fresh on
every click, and never touched by any cloud-sync code path in this
plugin** (confirmed with the user: this output must never be synced).

**Working here:** stay inside this plugin folder unless the change needs a
new top-level `core/` primitive. `maya-scripts/`'s output is read-only
from `core/`'s side (both just happen to agree on the same
`cache_dir`-derived folder).

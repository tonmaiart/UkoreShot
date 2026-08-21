# cache/plugins/UkoreShot/maya-scripts/

Configurable Maya playblast — **entirely a Maya-side tool** (confirmed with
the user 2026-07-19: no UkoreHub desktop UI at all, unlike `MayaPublisher`,
which pairs a Maya package with a Repository Setting tab of its own —
picking Publish Mode). Merged into this plugin 2026-08-20 from its own
former separate plugin, `cache/plugins/UkorePlayblast/` (own manifest id
`ukore_playblast`) — folded in because the two already shared everything
that mattered (same video-root folder, same naming convention) and the
Maya-side tool never had a UkoreHub UI of its own anyway. `../plugin.py`'s
`register(api)` contributes `PYTHONPATH` for this folder to the shared
`maya_launcher_env_bridge` so Maya can import `UkorePlayblast/`, plus a
`launch_hooks` entry that imports it every session so its own
`__init__.py` registers this tool's menu items — see `../plugin.py` and
`UkorePlayblast/__init__.py`. Replaced UkoreMaya's old hardcoded "Quick
Playblast" (Animation menu), which had no options dialog at all —
resolution, format/codec, quality, frame range, camera, sound, and
show-ornaments were all either hardcoded or implicit.

## Menu registration (no longer via MayaToolkit)

As of 2026-08-20, `UkorePlayblast/__init__.py` registers "Playblast" and
"Playblast Options..." directly into `ukore_menu`'s central "Ukore Tools"
registry (`UkoreMenu.registry.register_item()`), the same pattern most
other Maya tool plugins in this codebase use (see `ukore_menu/README.md`).
Previously this tool had no menu of its own at all — `MayaToolkit`'s
`UkoreMaya/core/menu_utils.py` had `playblast()`/`playblast_options()`
functions that lazily imported and called into this tool, and
`UkoreMaya/__init__.py` registered the actual menu items on this tool's
behalf. That wiring is now removed from `MayaToolkit` entirely. This tool
imports `UkoreMenu` lazily inside a try/except `ImportError`, the same
graceful-degradation pattern `UkoreMaya/__init__.py` itself already used —
a repo with `ukore_menu` not enabled just silently gets no menu item
rather than an import error, so `../manifest.json`'s `requires` doesn't
need to list it.

## Flat naming convention

As of 2026-07-20, a playblast lands **flat** in the repo's video root again
— no more `<sequence>/<shot_code>/vNNN/` subfolders (that scheme, from
earlier the same day, is superseded; see "Pre-2026-07-20 shot/version
subfolders" below for what happens to files it already wrote). All the
same information — sequence, shot, which take/pass this is, which output
this is within that take, and the version — now lives entirely in the
**filename** instead:

```
SEQ_ShotCode_Variation_index_version.ext
KBA_KBA030_Blocking_001_v001.mov
```

- **SEQ**/**ShotCode** — parsed off the current scene's basename the same
  way as before (`_SHOT_CODE_PATTERN`, a leading `letters+digits` run —
  `"KBA140_Anim_Layout_v001"` -> `"KBA"` / `"KBA140"`). Falls back to
  sequence `"misc"` + the whole (sanitized) scene basename as the shot
  code if the scene name doesn't parse (untitled scene, or a name that
  doesn't start with that pattern) — a deliberate fallback, not an error,
  so an oddly-named scene still produces a playblast.
- **Variation** — a per-repo choice from the "Playblast Options..."
  dialog's Variation dropdown: `layout`/`blocking`/`spline` built in
  (`options_store.BUILTIN_VARIATIONS`), plus whatever a repo has added via
  "Add..." (`options_store.add_variation`, saved per-repo — confirmed with
  the user 2026-07-20).
- **index**/**version** — see "Versioning" below. Both, along with
  sequence/shot/variation, are sanitized to letters-and-digits-only
  (`_sanitize_token`) before ever landing in a filename — the stem is
  split on `"_"` into exactly 5 parts by both this tool's own
  `_FILENAME_PATTERN` and `../core/video_naming.py` (the desktop-side
  reader of these same files), so nothing in the first three tokens may
  itself contain an underscore.

### Versioning

Each **exact** `(sequence, shot_code, variation)` triple has its own
independent version counter (confirmed with the user 2026-07-20 — e.g.
`Blocking` can be at `v005` while `Layout` on the same shot is still at
`v002`) — `_matching_versions` scans `video_root`'s top level (not
recursive — this convention has no subfolders of its own) for existing
files matching that exact triple via `_FILENAME_PATTERN`, building
`{version: [index, index, ...]}`.

- **Video** playblast: always a **new** version (`_next_version` = highest
  existing + 1, or `1`), index always `001` — one clip is the whole
  version.
- **Image (Current Frame)** playblast (see "Current-frame image mode"
  below): reuses whichever version **already exists** for that triple
  (`_latest_version`; creates `v001` if this is the first playblast for it
  at all) and takes the next **index** within that version
  (`_next_index`) — confirmed with the user 2026-07-20 that an image
  playblast "adds an index into the same version" rather than starting a
  new one, so a set of stills from one take ends up as `v001` index
  `001`, `002`, `003`... instead of each being its own version.

`_resolve_filename_stem` combines the above into the final stem;
`resolve_destination_path()` (the preview used by the options dialog's
destination label — a full **file** preview, not just a folder, since the
filename is where all the interesting information lives now) and
`publish_playblast()` both call it, so the preview always matches where
the file actually lands.

### Current-frame image mode

Added 2026-07-20: "Playblast Options..." has an Output section —
**Video** (existing behavior) or **Image (Current Frame)**. Image mode
captures **only** whatever frame the timeline's playhead is on right now
— not the saved frame range, not the whole timeline turned into an image
sequence — confirmed explicitly with the user ("มันจะเป็นโหมดสำหรับ
Current Frame เลย ไม่ใช่การเอา time slider มาแปลงเป็น image sequence
ทั้งหมด"). `function.py`'s `publish_playblast()` pins
`startTime`/`endTime`/`frame` all to `cmds.currentTime(query=True)` and
calls `cmds.playblast(format="image", compression=<image_format>, ...)`
— frame-range/sound options are ignored in this mode (and disabled in the
dialog, `_on_output_mode_changed`). Maya's `image` format always appends
its own frame-number suffix to the given filename (e.g.
`<stem>.0001.png`) with no documented way to suppress it — this is
undone afterward by `_finalize_single_frame_image`, which globs the
destination folder for whatever Maya actually produced and renames it to
the exact `<stem>.<image_format>` this convention expects, rather than
assuming a specific padding width (not guaranteed stable across Maya
versions).

### Image sequence generation moved to the desktop side

Added 2026-08-08, removed again 2026-08-20: Video mode used to run a
*second* `cmds.playblast(format="image", ...)` capture pass right after the
video capture, writing a `<video_root>/<stem>/<stem>.####.<image_format>`
sequence alongside the `.mp4` for frame-accurate review tooling
(UkoreShot/BananaSketch). Removed after the user confirmed
image-sequence generation should be `../interface/`'s (the desktop-side
video library/player's) own responsibility instead — see
`../core/video_sequence.py` — via ffmpeg, and only **lazily** (the first
time a video is opened in the Comment editor or Marked as Share), not on
every playblast whether anyone looks at it or not. This also removes the
"doubles viewport capture time" tradeoff the old approach had — a Maya
playblast is a single, plain capture pass again, same as before
2026-08-08. `publish_playblast()` locates whatever file Maya actually wrote
via `_locate_video_output` rather than assuming an extension, since "qt"/
"H.264" doesn't always write the same container across Maya versions.

### Pre-2026-07-20 shot/version subfolders

Playblasts already written under the old `<sequence>/<shot_code>/vNNN/`
scheme are **left exactly where they are** (confirmed with the user
2026-07-20, "ปล่อยไว้เหมือนเดิม") — nothing here migrates or renames them.
They're simply invisible to `_matching_versions`' flat top-level scan (so
they can never collide with a new flat-named file), and
`../core/video_naming.py`'s parser treats anything that doesn't match the
new flat convention as unparseable, bucketing it under "Unknown" in that
plugin's filter sidebar rather than erroring or hiding it.

## Files

- `UkorePlayblast/__init__.py` — registers this tool's "Playblast"/
  "Playblast Options..." `MenuItemSpec`s and a `ReloadHandlerSpec` into
  `ukore_menu`'s central registry, wrapped in the mandatory
  try/except `ImportError` (see "Menu registration" above). Must run at
  import time — triggered every Maya session by `../plugin.py`'s
  `pre_open_mel` launch hook, not merely present on `PYTHONPATH`.
- `UkorePlayblast/options_store.py` — `DEFAULT_OPTIONS` for any repo that
  hasn't opened "Playblast Options..." yet. Mostly reproduces the old
  hardcoded `publish_playblast` behavior, except `format`/`compression`:
  the old hardcoded `"qt"`/`"H.264"` values were changed to
  `"avi"`/`""` (uncompressed) on 2026-07-19 after a real "Unable to
  create a movie file" playblast failure — modern Maya on Windows has no
  QuickTime backend at all, so `"qt"` likely never actually worked there;
  `"avi"` needs no external codec framework, and leaving compression
  blank avoids assuming any specific codec is installed (applies to
  `output_mode == "video"` only, see above).
  `get_options`/`set_options`, constructing
  `PluginConfigStore(<active repo's own local clone>/.ukorehub/ukore_playblast.json)`
  straight off disk (same pattern `PublishApi.repo_paths` and
  `PublishApi.tickets` use — Maya's Python has no `PluginAPI` instance)
  under a plain `repo_options: {...}` key — committed to the repo's own
  git history, like `PublishApi`'s ticket storage, since these are
  team-shared playblast defaults, not a personal preference. Previously
  lived in a single shared, cloud-synced `data/plugins/core/ukore_playblast.json`
  file (every studio repo's options in one file, keyed
  `"<project_id>:<repo_id>"`) — moved out since that file needs Google
  Cloud Storage access this Maya process doesn't have; see
  `_migrate_from_shared_store` for how an existing repo's saved options
  are carried forward on first access. `"variation"` (this repo's
  currently-selected variation string) and `"output_mode"`/
  `"image_format"` (`"video"` | `"current_frame_image"`, and the image
  format used only in the latter) feed the flat naming convention above.
  `BUILTIN_VARIATIONS` (`layout`/`blocking`/`spline`) plus
  `get_variations`/`add_variation` manage each repo's own custom
  variation list separately, under its own `repo_variations` key (not
  mixed into `repo_options`, since it's a list a repo builds up over time
  rather than a single current setting) — `add_variation` sanitizes
  (`_sanitize_token`, letters/digits only, duplicated from `function.py`'s
  identical helper for the same reason `_repo_key` already is — too small
  to warrant a shared module) and returns the sanitized value actually
  saved so the dialog can select exactly that.
- `UkorePlayblast/options_dialog.py` — `PlayblastOptionsDialog` (`QDialog`,
  via `tmlib.module.PySide`'s version-aware PySide2/PySide6 shim and
  `tmlib.ui.interface_template.get_maya_window` for correct Maya-parented
  behavior — same Qt access pattern
  `MayaPublisher/maya-scripts/MayaPublisher/interface.py` uses). A
  "Naming / Output" group holds `variation_combo`
  (`options_store.get_variations`) + an "Add..." button
  (`_on_add_variation` -> `QInputDialog.getText` -> `options_store
  .add_variation` -> `_reload_variation_combo`, selecting the new value),
  and `output_video_radio`/`output_image_radio` (mutually exclusive via a
  `QButtonGroup`) + `image_format_combo` — toggling to Image (Current
  Frame) disables `format_box`/`frame_range_box`/`sound_check` via
  `_on_output_mode_changed` (none of them apply to a single-current-frame
  capture) and enables `image_format_combo` instead. Also: Resolution
  (render settings vs. custom width/height), format/compression, quality%,
  viewport-scale%, frame range (current timeline vs. custom start/end),
  camera (blank = active viewport), sound, show ornaments. A
  `destination_label` at the top (`_refresh_destination_label`) shows the
  full file path `function.resolve_destination_path()` currently resolves
  to for the active repo/scene/options, refreshed on open, after every
  playblast, and live as `variation_combo`/the output-mode radios/
  `format_combo`/`image_format_combo` change (those all feed the filename
  directly). A "Playblast" button sits in the button row alongside
  OK/Cancel (`QDialogButtonBox.ActionRole`, since it doesn't close the
  dialog) — `_on_playblast` saves the current widget values via the
  shared `_collect_options()` (also used by `_on_accept`) and calls
  `function.publish_playblast()` directly, so changes can be
  test-playblast'd without closing the dialog first. Saves on OK via
  `options_store.set_options`; `show()` is the module-level entry point
  `UkorePlayblast/__init__.py`'s registered menu command calls.
- `UkorePlayblast/function.py` — `publish_playblast()`, a single entry
  point for both Video and Image (Current Frame) output, branching on
  `options["output_mode"]` (see "Current-frame image mode" above).
  Resolves the active repo via `PublishApi.repo_paths.get_active_repo()`,
  the video root via `_resolve_video_root` (mirrors
  `../core/video_path_store.py`'s `resolve_video_root` exactly — a fixed
  per-machine folder under `PublishApi.repo_paths.find_cache_dir()`, keyed
  by project/repo, `mkdir`'d directly, no repo Custom Path or shared JSON
  store involved at all, so playblasts never travel through git/repo
  sync; duplicated rather than shared because Maya's `mayapy` interpreter
  can't import `../core/video_path_store.py` — that file needs a
  `PluginAPI` instance this process doesn't have), this repo's options via
  `options_store.get_options`, and the destination filename via
  `_resolve_filename_stem` (see "Flat naming convention" above — no more
  per-shot subfolder, `_resolve_video_root` itself ensures the flat
  `video_root` exists). `saved_path` is located afterward via
  `_locate_video_output` (globs for whatever extension Maya actually wrote,
  doesn't assume one — see "Image sequence generation moved to the desktop
  side" above for why `format`/`compression` changed to `"qt"`/`"H.264"`).
  `resolve_destination_path()` exposes just the active-repo/scene/options/
  filename resolution (no `os.makedirs`, no playblast) for
  `options_dialog.py`'s destination
  label. Prints `[UkorePlayblast]`-prefixed progress lines to Maya's
  Script Editor/console (start, resolved destination folder + filename,
  options in use, saved path or failure reason) so a playblast run is
  traceable without opening the dialog.

**Working here:** stay inside this `maya-scripts/` folder (or `../core/`
for `_resolve_video_root`'s desktop-side counterpart, read-only from this
side) unless the change genuinely needs `ukore_menu`'s own registry code.

# cache/plugins/UkoreShot/images/

This plugin's own icon files — a deliberate exception to the rest of the
codebase's `data/icons/` convention (see root `CLAUDE.md`'s "Project
layout" section): confirmed with the user 2026-07-21 that UkoreShot keeps
its icons local to the plugin instead of the shared folder every other
plugin's icons live in. Resolved from `../interface/player_widget.py` via
`_ICONS_DIR = Path(__file__).resolve().parents[1] / "images"` (one parent
up from `interface/player_widget.py` is `UkoreShot/` itself).

Same reasoning as root `CLAUDE.md`'s "never open the image directories"
rule for `data/thumbnails/`/`program_icons/` applies here
too — **don't open these PNGs speculatively**; they're binary assets, not
something a session needs to read to understand or modify this plugin's
behavior. If you need to know which icon a given button uses, check
`../interface/player_widget.py`'s icon-path constants
(`_PLAY_ICON_PATH`, etc.) instead of opening the file.

## Files

As of 2026-08-08, this plugin is view-only — the draw/comment editor's
own icons (Brush/Eraser/Text/Select/Undo/Redo/Clear Frame/comment-jump)
moved to `cache/plugins/BananaSketch/images/` along with the code that
used them. What's left, all from the [icons8](https://icons8.com)
"50"/"26" style families:

- `icons8-play-50.png` / `icons8-pause-50.png` — the play/pause toggle.
- `icons8-chevron-left-26.png` / `icons8-right-26.png` — previous/next
  frame.
- `icons8-edit-50.png` — the Edit Comment button (still the same icon —
  its meaning to an artist hasn't changed, only what it opens: BananaSketch
  instead of an in-app dialog, see `../interface/README.md`).
- `icons8-alphabetical-sorting-50.png` / `icons8-alphabetical-sorting-2-50.png`
  — the sort-by-name A-Z / Z-A buttons (`sort_az_button`/`sort_za_button`).
- `icons8-time-machine-32.png` / `icons8-delivery-time-32.png` — the
  sort-by-date Oldest / Newest buttons (`sort_oldest_button`/
  `sort_newest_button`).
- `icons8-grid-50.png` / `icons8-grid-2-24.png` — the Small / Large
  thumbnail view-mode buttons (`view_small_button`/`view_large_button`).
- `icons8-video-50.png` — the Sidebar tab icon for this whole section, set
  via `SectionSpec.icon_path` in `../plugin.py` (`_ICON_PATH`), not
  `player_widget.py`'s `_ICONS_DIR`.

Deleted 2026-08-08, no longer used anywhere in this plugin:
`icons8-paint-50.png`, `icons8-eraser-50.png`, `icons8-text-50.png`,
`icons8-cursor-24.png`, `icons8-delete-all-50.png`, `icons8-undo-30.png`,
`icons8-redo-30.png`, `icons8-double-left-26.png`,
`icons8-double-right-26.png` (moved to BananaSketch, still used there);
`icons8-hide-50.png`, `icons8-show-50.png` (the Show/Hide Comments toggle
they belonged to no longer exists anywhere — this plugin doesn't render
comment overlays at all now).

**Working here:** if a task needs a new icon for this plugin, add the PNG
directly to this folder (not `data/icons/`) and point a new
`_..._ICON_PATH` constant in `../interface/player_widget.py` at it via
`_ICONS_DIR`. Clean up "(1)"/"(2)" duplicate-download filename suffixes
before adding a file here, same housekeeping the rest of the codebase's
icon additions already follow.

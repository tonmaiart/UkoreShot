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
(`_BRUSH_ICON_PATH`, `_PLAY_ICON_PATH`, etc.) instead of opening the file.

## Files

All from the [icons8](https://icons8.com) "50"/"30"/"26" style families,
added 2026-07-20 for `PlayerWidget`'s toolbox/transport controls (see
`../interface/README.md`'s `player_widget.py` entry for exactly which
button uses which):

- `icons8-paint-50.png` / `icons8-eraser-50.png` / `icons8-text-50.png` —
  the Brush/Eraser/Text tool buttons.
- `icons8-chevron-left-26.png` / `icons8-right-26.png` — previous/next
  frame.
- `icons8-double-left-26.png` / `icons8-double-right-26.png` —
  previous/next commented frame.
- `icons8-undo-30.png` / `icons8-redo-30.png` — draw-canvas undo/redo.
- `icons8-play-50.png` / `icons8-pause-50.png` — the play/pause toggle
  (`icons8-pause-50.png` replaced an earlier `icons8-stop-50.png` choice
  2026-07-20 — a pause icon reads correctly for "play/stop" actually being
  a pause toggle, not a hard stop).
- `icons8-show-50.png` / `icons8-hide-50.png` — the view-mode Show/Hide
  Comments toggle.
- `icons8-edit-50.png` — the Edit Comment button.

Added 2026-08-03 for `video_library_page.py`'s library-panel toggle
buttons (see `../interface/README.md`'s `video_library_page.py` entry):
- `icons8-alphabetical-sorting-50.png` / `icons8-alphabetical-sorting-2-50.png`
  — the sort-by-name A-Z / Z-A buttons (`sort_az_button`/`sort_za_button`).
  The Z-A file was de-duped from a `"... (1).png"` download-suffix name per
  this folder's own housekeeping rule below.
- `icons8-time-machine-32.png` / `icons8-delivery-time-32.png` — the
  sort-by-date Oldest / Newest buttons (`sort_oldest_button`/
  `sort_newest_button`).
- `icons8-grid-50.png` / `icons8-grid-2-24.png` — the Small / Large
  thumbnail view-mode buttons (`view_small_button`/`view_large_button`).
- `icons8-video-50.png` — the Sidebar tab icon for this whole section, set
  via `SectionSpec.icon_path` in `../plugin.py` (`_ICON_PATH`), not
  `player_widget.py`'s `_ICONS_DIR` — this is the one icon in this folder
  that isn't a `PlayerWidget`/`video_library_page.py` button.

Added 2026-08-03 for `player_widget.py`'s Select tool and Clear Frame
buttons (`_SELECT_ICON_PATH`/`_CLEAR_ICON_PATH`):
- `icons8-cursor-24.png` — the Select tool button.
- `icons8-delete-all-50.png` — the Clear Frame button (replaced the earlier
  text-label fallback described in `../interface/README.md`'s
  `player_widget.py` entry).

**Working here:** if a task needs a new icon for this plugin, add the PNG
directly to this folder (not `data/icons/`) and point a new
`_..._ICON_PATH` constant in `../interface/player_widget.py` at it via
`_ICONS_DIR`. Clean up "(1)"/"(2)" duplicate-download filename suffixes
before adding a file here, same housekeeping the rest of the codebase's
icon additions already follow.

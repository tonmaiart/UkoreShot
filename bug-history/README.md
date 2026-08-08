# cache/plugins/UkoreShot/bug-history/

A plugin-scoped record of real bugs found and fixed specifically within
`cache/plugins/UkoreShot/`'s own code — the same convention as the
repo-root `developer/bug-history/README.md`, just narrowed to this plugin so a
session working only inside UkoreShot doesn't need to open the root
folder (which covers the whole app) to find the history relevant to it.
Added 2026-07-21 when this plugin's files were split into `core/`/
`interface/`/`images/`/`bug-history/` subfolders — see the top-level
`../README.md`'s "Structure" section.

**Before changing code in `../core/` or `../interface/`, check this
folder's index below** — if it's empty, also check the three root entries
listed under "Bugs fixed before this folder existed" below, since a
UkoreShot bug fixed before 2026-07-21 is recorded there instead (not
duplicated here — go read it in place at the repo root).

## Index

*(empty — no bug has been fixed in this plugin since this folder was
created. The next one fixed here gets its own entry, following the format
below.)*

## Bugs fixed before this folder existed

These remain at the repo-root `developer/bug-history/` (not duplicated here) since
they predate this local folder, and in two cases genuinely span more than
just this plugin's own code:

- [2026-07-20 — Playblast wrote into C:\\<name> instead of the repo's Custom Path](../../../../developer/bug-history/2026-07-20-playblast-custom-path-leading-slash.md)
  — spans this plugin's `core/video_path_store.py` *and*
  `plugins/repo_internal/UkorePlayblast/`'s Maya-side `function.py` (the same fix
  had to land in both independently — no shared code between them).
- [2026-07-20 — Draw overlay never received mouse input (two unrelated root causes)](../../../../developer/bug-history/2026-07-20-draw-overlay-native-video-widget.md)
  — `interface/player_widget.py`/`draw_overlay.py`, plus
  `plugins/core/DebugConsole/` and the app's top-level
  `core/extensibility/debug_log.py` (the diagnostic tooling used to find
  the second root cause). Its "Lesson" section is broadly reusable for any
  future Qt native-widget-stacking bug elsewhere in the app, which is why
  it stays at the root rather than being narrowed to this plugin only.
- [2026-07-20 — Repositioning a text box also drew a brush stroke at the same time](../../../../developer/bug-history/2026-07-20-text-tool-drew-strokes-simultaneously.md)
  — purely this plugin's `interface/draw_overlay.py`/`player_widget.py`.

## Adding a new entry

Same format the root `developer/bug-history/README.md` uses — one file per bug,
named `YYYY-MM-DD-short-slug.md`:

- **Symptom** — what the user actually observed/reported, in their words if useful.
- **Root cause** — the real mechanism, with file:line references.
- **Fix** — what changed and where.
- **Lesson** — the reusable pattern to watch for next time, not just a restatement of the bug.

Add the new file to the Index above in the same commit. If the bug's
"Lesson" is genuinely reusable *outside* this plugin (a general Qt/Python
gotcha, not something specific to UkoreShot's own architecture), add it to
the repo-root `developer/bug-history/` instead — that folder is what every other
session, working on any other plugin, actually checks.

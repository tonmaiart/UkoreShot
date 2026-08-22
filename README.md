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
  parses UkorePlayblast's flat `SEQ_ShotCode_index_version.ext` filenames
  (the `Variation` token was dropped from the convention 2026-08-21, per
  the user's own request — a pre-2026-08-21 file still carrying it just
  falls through to the "doesn't match" case, same as any other
  non-conforming name). `video_sequence.py` lazily extracts a video into a
  numbered
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
  viewer + Comment/Copy Clipboard/Get Video/Delete).
  `tableWidget_playblast_library` is multi-selectable (`ExtendedSelection`,
  2026-08-21) — `pushButton_delete_playblast` deletes every selected row's
  local video file + `sequence_dir` (comments.json included), confirmed
  with a dialog first; deleting a shared entry only ever removes the local
  copy (R2JsonSync has no delete-blob operation, so the cloud copy and its
  share code both keep working from any other machine — this is
  deliberate, not a TODO). Shift+A/Shift+D jump to the previous/next
  commented keyframe here too, same as `CommentEditor`'s own shortcut.
  **`pushButton_mark_as_share`/`pushButton_copy_clipboard` were merged into
  one button, `pushButton_copy_clipboard` (2026-08-22, per the user's own
  request)** — `_update_button_states` swaps its label between "Make
  Share" (entry not yet shared) and "Copy Code" (already shared), and
  `_on_copy_clipboard_clicked` dispatches to whichever action applies at
  click time instead of a separate handler wired to a separate button.
  **`pushButton_get_video_commented` was likewise removed and replaced by
  `checkBox_display_comment_download`, sharing `pushButton_get_video`**
  (2026-08-22) — checked routes the click through the "commented" render
  path (composited drawings + frame number, works off the sequence alone)
  instead of the plain one (needs a real local video file); see
  `_on_get_video_clicked`'s dispatch to `_export_plain_video`/
  `_export_commented_video` below. **`tableWidget_playblast_library` also
  gained a Share Code column** (2026-08-22) showing each entry's share
  code (blank if unshared), and the wildcard search bar now matches a
  video's share code as well as its stem — so pasting/typing an
  already-local code filters straight to that row without needing
  Enter/a cloud round-trip (typing a code that isn't local yet still goes
  through `_on_search_enter`'s existing pull-by-code path on Enter).
  **`tableWidget_playblast_library`'s Time Ago/Date columns were moved to
  the front of the table** (2026-08-22, per the user's own request) —
  `_COL_TIME_AGO`/`_COL_DATE` now come before `_COL_THUMBNAIL`/`_COL_NAME`/
  `_COL_SHARED`/`_COL_SHARE_CODE`; every other reference to a column is
  symbolic (via these `_COL_*` constants), so nothing else needed to change.
  **`lineEdit_copy_code`** (read-only, added to `UkoreShotPage.ui` 2026-08-22)
  shows the selected entry's share code (blank if unshared), kept in sync
  in `_update_button_states` alongside `pushButton_copy_clipboard`'s own
  label swap. **`pushButton_reload`/`_reload_videos_and_sync` now restores
  the previously-selected row after a Reload** (2026-08-22, per the user's
  own request) — `_reload_videos()` on its own still always resets
  selection to its "most recently modified" default (see that method's own
  docstring), so `_reload_videos_and_sync` captures `self._selected_key`
  before calling it and re-selects it via `_select_row_by_key` right after,
  same pattern `_on_shared_comments_synced` already used for the
  background-sync tail end of the same call. **Reload also now shows
  `widget_status_loading`** for the whole call (2026-08-22, same request) —
  covering the background `SyncSharedCommentsWorker` pull when one runs,
  not just the synchronous local rescan.
  `pushButton_sort_oldest` was removed from `UkoreShotPage.ui` 2026-08-21
  (name-ascending/newest-first are the only sort modes left).
  `checkBox_display_comment_overlay` (a plain `QCheckBox` since 2026-08-22 —
  was `pushButton_show_comment_toggle`, a checkable icon button with its own
  icon-swap logic (`_update_show_comment_icon`, `comment_mode.png`/
  `icons8-video-50.png`), replaced per the user's own request; the checkbox
  just uses its own `.ui`-authored label "แสดง Comment" instead, no icon
  swap needed anymore) toggles `player_widget.py`'s `_CommentOverlay` — a
  read-only rendering of the current frame's saved strokes over the video,
  the plain viewer's counterpart to `DrawOverlay`'s live drawing canvas,
  without needing to open the full `CommentEditor` (2026-08-21).
  `UkoreShotPage` caches the selected entry's whole `comments.json`
  "frames" dict once per selection (`_current_entry_frames`) rather than
  re-reading it from disk on every frame tick during playback.
  `plainTextEdit_comment` ("Current Keyframe Comment" box, added
  2026-08-21) always shows whichever text comment(s) are saved on the frame
  currently on screen — independent of `checkBox_display_comment_overlay`
  (that one only governs the drawing overlay) — one per line, refreshed via
  `_refresh_comment_text()` off the same cached `_current_entry_frames`
  `_refresh_frame_strokes` reads, so it stays cheap on every frame tick
  during playback too.
  `CommentEditor`'s Keyframe Comment table no longer auto-adds a row for
  whichever frame the player happens to be on (removed 2026-08-21, per the
  user's own request) — a frame only ever gets a row once it actually has
  a saved comment or drawing (`_keyframe_indices`), not just for being on
  screen; clearing a comment's text down to blank now removes that comment
  entirely (`_apply_comment_text`) instead of leaving a meaningless
  blank-text one behind, so a frame left with neither a comment nor a
  drawing disappears from the list on its own via
  `_record_frame_change`'s existing "pop the frame if empty" logic.
  `pushButton_clear_frame`/`pushButton_edit_message` always operate on
  whichever frame the player is currently on
  (`self._current_frame_index`) — never whatever row happens to be
  selected in the table, which is a separate, independently-driven piece
  of state that can drift out of sync with it. `pushButton_edit_message`
  also works with no table row selected at all (edits the current frame's
  first existing comment if it has one, else composes a new one). Both
  buttons went back to plain, icon-less buttons the same day, using
  `CommentEditor.ui`'s own original labels ("Clean Draw"/"Edit Message")
  instead of `_set_button_icon`'s usual icon-only look. Edit Message opens
  `_EditCommentDialog` (own class in comment_editor.py) instead of the old
  single-line `QInputDialog.getText` — a `QPlainTextEdit` so a long
  comment wraps instead of scrolling sideways, Enter inserts a newline
  instead of submitting, and a "Clear Message" button empties the field
  without closing the dialog. `tableWidget_comment`'s Comment column wraps
  long text onto multiple lines too (`setWordWrap(True)` +
  `resizeRowsToContents()` after every `_refresh_table()` repopulation)
  instead of clipping it to one line.
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
  shared. Registers only "Ukore Shot Playblast" (General category) into
  `ukore_menu`'s central "Ukore Tools" registry — the old "Playblast
  Options..." (Anim category) item, its dialog (`options_dialog.py`), and
  its per-repo settings store (`options_store.py`) were all removed
  2026-08-21, per the user's own request: every playblast setting is now
  hardcoded in `function.py`, nothing left to configure per repo/artist.
  Hardcoded settings: qt/H.264 at 80% quality, 80% viewport scale, current
  timeline frame range (no `startTime`/`endTime` override — Maya's own
  playblast default), active viewport camera (never overridden), sound
  included when the scene has an audio node, ornaments/HUD off, and no
  variation token in the filename anymore (see "Flat naming convention"
  below). `publish_playblast()` also forces the scene's render settings to
  Arnold + the HD 1080 image size preset (`_apply_render_settings` —
  `defaultRenderGlobals.currentRenderer` + `defaultResolution`'s
  width/height/deviceAspectRatio/pixelAspect) before every capture, and the
  playblast's own width/height are read back off `defaultResolution`
  afterward, so the two can never disagree. `_enable_gate_display`/
  `_restore_gate_display` turn Resolution Gate + Gate Mask **on** for every
  camera in the scene for the duration of the capture (2026-08-21,
  reversing the tool's previous hide-the-gates behavior, per the user's own
  request — the render frame is now meant to be visibly burned into the
  playblast), recording each attribute's prior value first and restoring it
  afterward either way, success or failure.
  `_resolve_filename_stem`'s version/index pick is a scan-based guess
  (`_matching_versions`, regex against existing filenames) that can miss
  an existing file whose actual on-disk name doesn't cleanly match the
  expected shape — `_stem_is_free` is a second, independent filesystem
  check that keeps bumping the candidate stem until it's genuinely free,
  so `cmds.playblast()` (whose `forceOverwrite` is deliberately never set
  — overwriting an existing playblast is never correct, only picking a
  new name is) never aborts with a real "file exists" error (fixed
  2026-08-21, a real user report).
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
`pushButton_copy_clipboard`, while the selected entry isn't shared yet —
merged into that one button 2026-08-22, see the `interface/` bullet above)
uploads that sequence + `comments.json` to
Cloudflare R2 via `api.cloud_sync` (`core/share_sync.py`'s
`ShareUploadWorker`), then generates and persists a
`UKSHOT_{ShotCode}_v{version}_{4 hex chars}` code
(`comment_store.generate_share_code` — the `UKSHOT_` prefix was added
2026-08-21, per the user's own request, so a pasted code reads as
unambiguously this plugin's own; a code generated before that change has
no prefix and was never migrated to add one, it just keeps working as-is
— `video_library_page.py`'s `_SHARE_CODE_PATTERN` matches both shapes)
and pushes a small pointer blob (`share_sync.push_pointer`) that makes the
code resolvable. "Copy Share Code" copies that code as plain text.
`share_sync.generate_unique_share_code` (2026-08-21, per the user's own
request that a generated code must never collide with one already in the
cloud) is what `_on_mark_as_share_clicked` actually calls instead of
`comment_store.generate_share_code` directly — R2JsonSync has no way to
*enumerate* the bucket to rule collisions out up front, so it checks each
freshly-generated candidate against the cloud (`pull_pointer`, non-`None`
means taken) and retries with a fresh random suffix on an actual
collision, up to `_MAX_CODE_GENERATION_ATTEMPTS` (10, practically never
exhausted). `_on_share_upload_succeeded`/`_on_share_upload_failed` also
now take the shared entry's own `entry.key` and explicitly re-select that
exact row (`_select_row_by_key`) after their own `_reload_videos()` call —
`_reload_videos()` always resets selection to its "most recently
modified" default, which usually *is* the entry just shared but isn't
guaranteed to be, so without this a later `pushButton_copy_clipboard`
click could copy a *different* entry's code than the "Copy Code and
Close" dialog just showed (fixed 2026-08-21, a real user report).

`ShareUploadWorker`/`PullByCodeWorker` push/pull up to
`_MAX_CONCURRENT_TRANSFERS` (6) frame files at once via a
`ThreadPoolExecutor`, not one file at a time sequentially (2026-08-21,
speeding up sharing a shot with hundreds of frames) — boto3/botocore
clients are thread-safe for concurrent calls, so this is a pure wall-clock
win. Every file still gets attempted even if one fails; the first real
(non-`ConflictError`) exception seen across the batch is what actually
gets raised/reported.

**Keeping locally-pulled comments in sync (2026-08-21, per the user's own
request):** `_reload_videos()` alone only ever re-scans local disk, never
the cloud — pasting an already-local share code into the search bar is
also a no-op (`_on_search_enter`'s own "already local, skipping pull"
check), so without this there was no way to see someone else's newer
comments on an already-shared video short of deleting the local copy and
re-pasting its code. `pushButton_reload` and `set_repo` (tab open/repo
switch) now call `_reload_videos_and_sync` instead of `_reload_videos`
directly — it reloads local state first (fast, unchanged), then kicks off
`share_sync.SyncSharedCommentsWorker` in the background to pull just
`comments.json` (not the whole frame sequence, which never changes once
shared) for every locally-shared entry at once, concurrently, same
`_MAX_CONCURRENT_TRANSFERS` pattern as the upload/pull workers. Silent/
best-effort per entry — a single failed pull doesn't surface a dialog,
since this runs automatically and often. `_on_edit_comment_clicked` also
calls `share_sync.pull_comments` synchronously (with its own "Syncing
comments..." status message) to resync just the one entry being opened,
right before `CommentEditor` constructs — also best-effort, a failed
resync still lets the editor open against whatever's already local rather
than blocking. Both reuse the same cloud-always-wins, no-merge-story
assumption `CommentSyncWorker`'s own push side already makes. Every
`_reload_videos()` call site that isn't Reload/tab-open/Edit-Comment-open
(delete, pull-by-code success, share-upload success/failure) deliberately
stays plain `_reload_videos()` — a full re-sync of every *other* entry
isn't warranted for those.

Pasting that same code into `lineEdit_search_bar` and pressing Enter on a
*different* machine pulls the sequence + `comments.json` back down
automatically (`share_sync.PullByCodeWorker`) — the video shows up in the
table from its pulled sequence alone, with no local `.mp4` ever existing
for it on that machine. Saving a comment in `comment_editor.py` on a video
that's already shared also pushes just the updated `comments.json`
incrementally (`share_sync.CommentSyncWorker`) rather than requiring a
fresh Mark as Share for every comment.

## Get Video / Get Video - Commented

Added 2026-08-21: `pushButton_get_video` in `video_library_page.py`
generates a fresh, hard-capped-at-20MB `.mp4` on click, routed to either
export path below depending on `checkBox_display_comment_download`
(merged from a separate `pushButton_get_video_commented` button
2026-08-22, see the `interface/` bullet above), then shows a single-button
"Ok and Show me in explorer"
dialog (`_notify_export_ready`, so Explorer popping open doesn't catch
anyone off guard) before revealing+selecting it in Windows Explorer
(`explorer /select,`). Get Video burns the frame number into every frame
via ffmpeg's `drawtext` while *also* targeting `_MAX_EXPORT_BYTES` in that
same single ffmpeg call (`core/video_compress.py`'s `burn_frame_numbers`,
which computes the same duration-based bitrate `compress_to_fit` itself
uses via the shared `_calculate_video_bitrate` — merged from an earlier
burn-then-compress two-pass version the same day, since burning text
already forces one full re-encode regardless of the source's own size, so
targeting the byte cap in that same pass avoids a second, redundant
re-encode). `compress_to_fit`/`burn_frame_numbers` both pass
`-preset veryfast` to libx264 (not the slower `medium` default) — a
quick local export doesn't need mastered-quality encoding efficiency.
Get Video - Commented composites each extracted frame's saved drawing
(`draw_overlay.py`'s `paint_stroke_points`) *and* its frame number
(`player_widget.py`'s `paint_frame_number`, the same module-level function
`_FrameNumberOverlay`'s live HUD itself calls, so the burned-in number
matches the live viewer's look exactly) onto its own image first, then
encodes that sequence back into a video (`core/video_sequence.py`'s
`encode_sequence_to_video`) before the same compression pass — both paths
burn the number in regardless of whether the video actually has any saved
comments/strokes (2026-08-21, per the user's own request). Both write
into a dedicated per-repo export folder (`core/video_path_store.py`'s
`resolve_export_dir`, a sibling of the video library root, never scanned
by it) — **local-only, overwritten fresh on every click, and never
touched by any cloud-sync code path in this plugin** (confirmed with the
user: this output must never be synced).

**Working here:** stay inside this plugin folder unless the change needs a
new top-level `core/` primitive. `maya-scripts/`'s output is read-only
from `core/`'s side (both just happen to agree on the same
`cache_dir`-derived folder).

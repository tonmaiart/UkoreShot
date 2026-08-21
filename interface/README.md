# cache/plugins/UkoreShot/interface/

Every PySide6 widget/page for the UkoreShot plugin — no data persistence
or path-resolution logic lives here directly, that's `../core/` (imported
as `from ukoreshot_plugin.core import ...`). Split out from the plugin's
flat file layout on 2026-07-21 — see the top-level `../README.md`'s
"Structure" section for the full rule on reading only the subfolder a
task actually needs.

**Rebuilt 2026-08-20** against two user-authored Qt Designer `.ui` files
(`UkoreShotPage.ui`, `CommentEditor.ui`), loaded at runtime via `QUiLoader`
— same pattern the host app's `plugins/core/explorer/browser_widget.py` and
`plugins/core/ExternalPluginManager/external_plugins_page.py` already use
(the `.ui` only supplies layout/widget identity, not behavior; an empty
placeholder `QGroupBox` in each `.ui` gets a real widget — `PlayerWidget` —
inserted into it at runtime). This rebuild also brought the draw/comment
editor back in-house (it was extracted to the separate `BananaSketch`
plugin on 2026-08-08) and added image-sequence-based cloud sharing. If a
task is about drawing/commenting on a frame, it's `draw_overlay.py`/
`comment_editor.py` in *this* plugin again now, not `BananaSketch`.

## Files

- `video_library_page.py` — `UkoreShotPage`, the section's top-level
  widget. `groupBox_playblast_viewer` (from `UkoreShotPage.ui`) holds a
  plain `PlayerWidget`; `tableWidget_playblast_library` (columns Thumbnail/
  Name/Shared/Date/Time Ago) replaced the old `FlowLayout` card grid +
  `FilterSidebar` entirely — confirmed with the user that per-category
  filtering is retired in favor of just `lineEdit_search_bar` (wildcard via
  `fnmatch`, plain typing still behaves like the old substring search) +
  the four sort buttons. A `_LibraryEntry` row is either a real local video
  file (the common case) or a sequence-only folder that arrived purely via
  a pasted share code (`core/share_sync.py`'s `PullByCodeWorker` — no local
  video file exists for it at all).

  **Video->image-sequence splitting is lazy** (`core/video_sequence.py`,
  ffmpeg-based) — confirmed explicitly with the user: a video plays from
  its original file by default, and is only ever split into a sequence the
  first time `pushButton_comment` or `pushButton_mark_as_share` is clicked
  for it (`_ensure_sequence_for`). `_reload_videos`/`_apply_filter` never
  trigger it — browsing the library alone never costs an ffmpeg call.

  `pushButton_comment` (also `PlayerWidget.editCommentRequested`, still
  emitted the same signal-out way as before) calls `_ensure_sequence_for`
  then opens `comment_editor.py`'s `CommentEditor` directly — this replaces
  the 2026-08-08 `BananaSketch` hand-off (`UICommandService.navigate_and_focus`)
  entirely; `set_host`/`_host` are gone from this page.

  `pushButton_mark_as_share` extracts the sequence if needed, uploads it +
  `comments.json` via `core/share_sync.py`'s `ShareUploadWorker`
  (`api.cloud_sync` — see `plugin-api.md`'s "What's deliberately not
  re-exported" section for why that's the only sanctioned way a plugin
  touches R2), then generates/persists a `comment_store.generate_share_code`
  code and pushes the small `share_codes/<code>.json` pointer blob
  (`push_pointer`) that makes the code resolvable at all — `R2JsonSync` has
  no "list" operation, so without this pointer nothing could ever map a
  pasted code back to its blobs. `pushButton_copy_clipboard` copies that
  code as plain text (no web viewer exists to link to instead).

  `lineEdit_search_bar`'s `returnPressed` (Enter) is a *second* behavior
  layered on top of live wildcard filtering: if the typed text matches
  `generate_share_code`'s exact shape and isn't already local, it runs
  `PullByCodeWorker` to pull that video's sequence + `comments.json` down
  from the cloud automatically — confirmed with the user this round.

  `pushButton_get_format_video`/`pushButton_auto_send_to_discord` reuse
  `../core/video_compress.py`/`../core/discord_client.py`/
  `discord_send_worker.py` largely unchanged from the pre-2026-08-20
  player-embedded Discord button, just retriggered from these page-level
  buttons instead.

- `player_widget.py` — `PlayerWidget` + `_VideoSurface` + `_FrameNumberOverlay`
  + `_VideoStack`. **Dual playback source** (added 2026-08-20):
  `load_video(path)` still drives `QMediaPlayer`/`QVideoSink` exactly as
  before (the common "just browsing" case — never forces a sequence
  extraction); `load_sequence(sequence_dir)` instead drives
  `sequence_player.py`'s `SequencePlayer` over an already-extracted image
  sequence, used by `CommentEditor` always (drawing/comments need an exact,
  machine-independent frame index — `QMediaPlayer`'s position-based
  estimate can't guarantee that across machines/codecs) and by this page
  only for a cloud-pulled sequence-only entry. `self._mode` ("video" |
  "sequence" | `None`) is what every transport handler (play/pause/step/
  seek/speed) dispatches on; the transport row/shortcuts/layout are
  identical either way.

  **`show_edit_tools=True`** (constructor-only, used exclusively by
  `comment_editor.py`) revives the pre-2026-08-08 edit mode: `_VideoStack`
  grows a third `DrawOverlay` layer (see `draw_overlay.py`) between the
  video surface and the frame-number HUD, plus a toolbar row (brush/eraser/
  text/select, color swatch, size slider, undo/redo, Clear) built in code
  above the video. `edit_comment_button`/`send_discord_button` are hidden
  in this mode — neither makes sense from inside the editor itself.
  `frameIndexChanged(int)` fires on every sequence-mode frame change —
  `comment_editor.py` uses it to load/save the right frame's
  strokes/text-boxes as the user scrubs.

- `sequence_player.py` — `SequencePlayer(QObject)`, added 2026-08-20. Reads
  numbered frame images off disk on every `seek()`/`step()`/timer tick (no
  preloading) and emits `frameChanged(frame_index, QImage)`. `fps` comes
  from `comments.json`'s `share.fps` if a video's ever been shared, else a
  24.0 fallback. `playbackStateChanged(bool)` covers both explicit
  play()/pause() and the timer naturally auto-pausing at the last frame —
  `player_widget.py` needs both to keep its play/pause icon in sync.

- `draw_overlay.py` — `Stroke`, `_TextBoxItem`, `DrawOverlay`,
  `ReadOnlyCommentOverlay`, `paint_stroke_points`. Revived 2026-08-20 near-
  verbatim from git history (commit `f9b3505` in `UkoreHubDev`, before this
  code was extracted to `BananaSketch` on 2026-08-08) — brush/eraser/text/
  select tool state machine, normalized-0-1-point strokes, snapshot
  undo/redo (not persisted, resets per frame). No direct file I/O — the
  embedding widget (`comment_editor.py`) owns persistence, same as before.

- `comment_editor.py` — `CommentEditor(QDialog)`, new 2026-08-20, wraps
  `CommentEditor.ui`. Operates entirely on a `sequence_dir` (never a video
  path) — `video_library_page.py` always calls `_ensure_sequence_for`
  first. **`pushButton_save_comment`/`pushButton_cancel_comment` are
  dialog-level batch commit/discard**, not the old system's save-on-every-
  keystroke: every stroke/text-box/comment edit only mutates `self._frames`
  in memory (`_record_frame_change`) until Save, which writes it via
  `../core/comment_store.py::save` and — only if the video is already
  shared — pushes just the updated `comments.json` to the cloud via
  `CommentSyncWorker` (confirmed with the user: saving a comment on an
  already-shared video should sync incrementally, not require a fresh Mark
  as Share). Cancel discards everything, no disk/cloud write at all. The
  `tableWidget` ("Keyframe Comment") is one row per existing comment
  (Frame/Author/Comment/Time) plus a trailing blank row — double-click its
  Comment cell to add a new comment on whichever frame the player currently
  shows; double-click an existing row to edit it in place; right-click for
  Delete Comment.

- `thumbnail_loader.py` — `ThumbnailLoader`: unchanged from before —
  grabs a video file's first decodable frame as a `QPixmap`. A sequence-only
  entry (no video file) instead loads its own first frame image directly in
  `video_library_page.py`, bypassing this class.
- `discord_send_worker.py` — `DiscordSendWorker`: unchanged. Still the
  compress-if-needed → find-or-create-forum-post → send-video chain,
  just triggered from `video_library_page.py`'s page-level Discord buttons
  now instead of a button embedded in `PlayerWidget`.
- `repo_video_settings_page.py` — `RepoVideoSettingsPage`, the
  `CATEGORY_REPO` Settings tab (Discord Channel ID/Bot Token/Max Upload
  Size/ffmpeg Path — the same ffmpeg path this plugin's own
  `core/video_sequence.py` extraction and `core/video_compress.py`
  compression both reuse). Unchanged by this rebuild.

**Working here:** stay inside `interface/` unless the change needs a new
top-level `core/` primitive (the app's own) or a `../core/` addition this
UI depends on.

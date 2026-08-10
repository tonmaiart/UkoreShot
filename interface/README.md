# cache/plugins/UkoreShot/interface/

Every PySide6 widget/page for the UkoreShot plugin — no data persistence
or path-resolution logic lives here directly, that's `../core/` (imported
as `from ukoreshot_plugin.core import ...`). Split out from the plugin's
flat file layout on 2026-07-21 — see the top-level `../README.md`'s
"Structure" section for the full rule on reading only the subfolder a
task actually needs.

As of 2026-08-08, this plugin is a **plain video library + player only**
— the entire draw/comment editor (`DrawOverlay`, `CommentThread`,
`CommentSidebar`, `EditVideoDialog`, plus everything `player_widget.py`'s
old `show_edit_tools=True` branch built) was extracted into its own
standalone plugin, `cache/plugins/BananaSketch/`. If a task is about
drawing on a frame, per-frame comments, or anything that used to live in
`EditVideoDialog`, it belongs in that plugin now, not here — see its own
`interface/README.md`.

## Files

- `repo_video_settings_page.py` — `RepoVideoSettingsPage`, the
  `CATEGORY_REPO` Settings tab. Same self-resolving-active-repo `refresh()`
  + "list of choices, auto-use if there's only one" pattern as
  `plugins/repo_internal/RigPublisher/settings_page.py`'s
  `RigPublisherSettingsPage`, just pointed at `custom_paths` instead of
  `pipeline_inputs` (via `../core/video_path_store.py`). Also owns a
  `QGroupBox("Discord")` section (for the "Send to Discord" button, see
  `player_widget.py`/`video_library_page.py` below) — labeled "Forum
  Channel ID" in the UI, since it must be a Discord Forum Channel, not a
  plain text channel (see `discord_note`'s own text and
  `../core/discord_client.py`'s `find_or_create_forum_post`): Channel ID
  and Bot Token are **both** per-repo, both refreshed together by
  `_refresh_discord_fields()` alongside the Custom Path list
  (`../core/discord_client.py`'s `get_channel_id`/`set_channel_id`/
  `get_bot_token`/`set_bot_token`), both re-enabled/cleared together based
  on whether a repo is active. Unlike Channel ID, Bot Token is a real
  secret that this page still displays and lets you edit in place
  (`discord_token_edit` is pre-filled from the saved value, not
  write-only) — confirmed with the user 2026-08-08 as the deliberate
  tradeoff for every machine picking it up automatically via git: it's
  committed to the studio repo in plain text, so `discord_note`'s label
  says so plainly rather than implying any real secrecy. Also added
  2026-08-08 (after a real "video too large" send failure): Max Upload
  Size (`discord_max_upload_spin`, a `QSpinBox`, per-repo/shared, same
  refresh/enable lifecycle as Channel ID/Bot Token) and ffmpeg Path
  (`discord_ffmpeg_edit`, **per-machine**, loaded once in `__init__` —
  not `_refresh_discord_fields()`, since it isn't repo-scoped at all, see
  `../core/discord_client.py`'s `get_ffmpeg_path`/`set_ffmpeg_path`).
- `video_library_page.py` — `UkoreShotPage`, the section's top-level
  widget, plus `_VideoCard`. Its Sidebar tab icon is `icons8-video-50.png`,
  wired up via `SectionSpec.icon_path` in `../plugin.py`, not from this
  file. `PlayerWidget()` (top half — `content_layout` gives
  `player_panel`/`library_panel` equal `stretch=1` so the page always
  splits 50/50) handles plain playback for whichever video is selected —
  labeled `player_title`, a `QLabel("Playblast Viewer")` (objectName
  `ukoreShotSectionTitle`, styled via `core/theme.py`) placed above it in
  `player_panel`. Edit Comment lives inside `PlayerWidget` itself as a
  square icon button — this page connects to
  `player_widget.editCommentRequested` and, since 2026-08-08, calls
  `self._host.navigate_and_focus("banana_sketch", self._selected_card.video_path)`
  to open BananaSketch (a separate plugin — see `set_host`, called once
  from `../plugin.py`'s `_wire`) instead of opening an in-app dialog. This
  page has no idea what BananaSketch actually does with that path — it
  just hands off the file and moves on.

  Bottom half (`library_panel`) is one vertical stack headed by
  `library_title`, a `QLabel("Playblast Library")` (same
  `ukoreShotSectionTitle` styling as `player_title`), then `filter_sidebar`
  (a `FilterSidebar` — see that file), then `controls_row` (Refresh, then
  the four sort buttons — `sort_az_button`/`sort_za_button`/
  `sort_oldest_button`/`sort_newest_button`, one exclusive `QButtonGroup`,
  `_sort_videos` applies whichever's checked — then, right-aligned via a
  stretch, the two view-mode buttons `view_small_button`/`view_large_button`,
  another exclusive `QButtonGroup` picking a `_CARD_SIZES` preset), then
  the vertically-scrolling wrapping `cards_layout` grid of `_VideoCard`s
  (`FlowLayout`, `flow_layout.py`, not a plain box layout, so cards wrap
  onto new rows and the grid grows downward — built via
  `interface/shared/widget_helpers.wrap_scrollable`, the **app's**
  top-level `interface/` package again, not this folder — with the
  horizontal scrollbar forced off, `cards_scroll` given `stretch=1`).
  `_VideoCard` takes `card_width`/`thumbnail_height` as constructor args
  (one of `_CARD_SIZES["small"]`/`["large"]`, chosen by the view-mode
  buttons), so switching the toggle rebuilds the grid at a different size.

  `_selected_video_path` (alongside `_selected_card`, a `_VideoCard`
  reference torn down and rebuilt by every `_clear_cards()`) survives a
  `_apply_filter()` rebuild and is what `_restore_or_default_selection`
  uses to keep whichever video was selected across a filter/sort/
  view-size change if it's still in the rebuilt list — falling back to
  the most recently modified video (`p.stat().st_mtime`) whenever there's
  no prior selection, which is what makes "opening the UkoreShot tab
  always shows the latest playblast" work: `_reload_videos` (called from
  `set_repo`, itself called on every repo switch or tab refocus)
  explicitly resets `_selected_video_path` to `None` before its own
  `_apply_filter()` call, so that first population after a repo
  load/refocus always hits the "no prior selection" fallback branch.

  All six of `controls_row`'s sort/view buttons are `QToolButton` (not
  `QPushButton`) with an icon from `../images/` set via
  `PlayerWidget._set_button_icon` (reused rather than duplicated —
  `PlayerWidget` is already imported here) —
  `icons8-alphabetical-sorting-50.png`/`icons8-alphabetical-sorting-2-50.png`
  (A-Z/Z-A), `icons8-time-machine-32.png`/`icons8-delivery-time-32.png`
  (Oldest/Newest), `icons8-grid-50.png`/`icons8-grid-2-24.png`
  (Small/Large). `QToolButton` rather than `QPushButton` matters here
  because `core/theme.py`'s `QToolButton:checked` rule (accent background)
  is what actually shows which single choice in each exclusive
  `QButtonGroup` is active.

  `_reload_videos` scans `resolve_video_root(...)` (`../core/video_path_store.py`)
  recursively for every `.mov`/`.mp4`/`.avi` file — a video flat-named
  under UkorePlayblast's naming convention lives directly in the video
  root, but an older playblast may still sit nested under its own
  `<sequence>/<shot_code>/vNNN/` subfolder (left alone there per the
  user's own decision — see `UkorePlayblast/README.md`), and both need to
  show up here. UkorePlayblast's newer image-sequence output (added
  2026-08-08, see that plugin's README) lands in a `<stem>/` subfolder
  next to the video — this scan doesn't descend into it looking for
  anything (its files aren't `.mov`/`.mp4`/`.avi`), so it's invisible here
  by construction; teaching this page to actually use that sequence is
  separate, not-yet-scheduled work. For each video it also caches
  `video_naming.parse_video_filename(video_path)` (`../core/video_naming.py`,
  `None` for anything that doesn't match the convention — a legacy nested
  file, most likely) in `_parsed_by_video`, consulted by
  `_video_matches_filters` and `_collect_filter_values` (which feeds
  `filter_sidebar.set_available_values`). `_apply_filter` (no-argument,
  triggered by `filter_sidebar.filtersChanged` or either button group)
  rebuilds the card grid from `_all_videos` filtered through
  `_video_matches_filters` (AND across categories, OR within one — see
  `filter_sidebar.py`'s own docstring) and search text, then sorted via
  `_sort_videos`. `_format_filter_value` is the single place index/version
  get their zero-padded display form (`"003"`/`"v001"`, matching how they
  actually look in the filename) — used identically by both
  `_collect_filter_values` (building the choice list) and
  `_video_matches_filters` (matching a selection against it), so the two
  can never disagree on formatting. A video that doesn't parse
  (`_parsed_by_video[path] is None`) contributes `"Unknown"` to every
  naming-derived filter category rather than being excluded from
  filtering or hidden from the grid.

  Each `_VideoCard` (`QFrame`, object name `videoCard`, styled via
  `core/theme.py`'s `QFrame#videoCard` rules — the app's top-level
  `core/theme.py`) paints its best-effort thumbnail (`thumbnail_loader.py`)
  fill-cropped into a fixed-height top strip, with the video's path
  relative to the video root (filename bold, parent folder secondary/gray)
  underneath as normal child labels. Implements the standard
  `set_repo(project, repo, workspace_root)` page protocol
  (`interface/main_window.py`'s `_apply_to_current_page`), plus
  `set_host(host)` (called once at startup by `../plugin.py`'s `_wire`)
  for the BananaSketch handoff above.
- `filter_sidebar.py` — `FilterSidebar`: the library's filter panel, one
  multi-select `QListWidget` per category (Sequence, Shot Name, Variation,
  Index, Version — `_CATEGORIES`), plus a `search_edit` free-text box
  above them. Selecting several values within one category is OR;
  selecting across categories is AND — this widget only exposes the raw
  state (`selected_values(category)`, `search_text()`) and a single
  `filtersChanged` signal, `video_library_page.py`'s
  `_video_matches_filters` is what actually combines them.
  `set_available_values(values_by_category)` rebuilds every list from
  scratch (called after every `_reload_videos()` rescan) while preserving
  selection for values that still exist, so a Refresh doesn't silently
  clear an active filter. Used to have a sixth "Commented By" category —
  dropped 2026-08-08 along with `comment_store.list_commenters`, its data
  source, when the draw/comment editor moved to `cache/plugins/BananaSketch/`.

  Laid out horizontally — one `_COLUMN_WIDTH`-wide column per category,
  packed left-to-right in a `categories_row` `QHBoxLayout` below
  `search_edit`, placed as its own row directly above
  `video_library_page.py`'s `controls_row`.
- `flow_layout.py` — `FlowLayout`, Qt's well-known "Flow Layout" `QLayout`
  recipe (packs children left-to-right, wraps to a new row on overflow,
  `heightForWidth`-driven so a `QScrollArea(widgetResizable=True)` around
  it grows/scrolls vertically). Generic — no UkoreShot-specific code in it
  — reused as-is by `video_library_page.py`'s card grid.
- `thumbnail_loader.py` — `ThumbnailLoader`: one hidden `QMediaPlayer` +
  `QVideoSink` pair, processed one video at a time, grabs the first
  decodable frame as a `QPixmap` for the list icon. Best-effort — a video
  it can't decode a frame from just shows with no icon, nothing crashes.
- `player_widget.py` — `PlayerWidget` + `_VideoSurface` +
  `_FrameNumberOverlay` + `_VideoStack`: plain playback, nothing else.
  `QMediaPlayer` + `_VideoSurface` (a plain `QWidget`, not `QVideoWidget` —
  see its own docstring and the host app's own
  `developer/bug-history/2026-07-20-draw-overlay-native-video-widget.md`;
  `QVideoWidget` renders through a native OS window handle that always
  wins Z-order/mouse-hit-testing over ordinary sibling widgets, which used
  to silently eat every click meant for this plugin's own drawing overlay
  before that moved out — kept unchanged here since it's still the right
  way to paint video frames manually, even with nothing left to protect
  from Z-order issues). `_VideoSurface` paints each frame itself via
  `QMediaPlayer.setVideoSink(QVideoSink)` + `QVideoSink.videoFrameChanged`
  -> `QVideoFrame.toImage()`, the same frame-grab `thumbnail_loader.py`
  already uses for its one-shot thumbnails, just applied continuously.
  `_VideoSurface` and `_FrameNumberOverlay` are stacked by `_VideoStack` —
  plain manual `setParent`/`show()`/`setGeometry`/`raise_()` on every
  resize, not `QStackedLayout`, simplified 2026-08-08 to two layers
  (`base`/`hud`) now that there's no third drawing-overlay layer to carry.
  `frame_number_overlay.set_frame_index` is called from
  `_on_position_changed` whenever the frame index actually changes. Text
  is bold, white, with a black stroke drawn outside the fill
  (`_STROKE_WIDTH`) via the "poor man's outline" ring-of-offsets technique
  — see the class's own docstring.

  `transport_row`: `prev_frame_button`, `play_button`, `next_frame_button`
  (`icons8-chevron-left-26.png`/`icons8-play-50.png`/`icons8-pause-50.png`/
  `icons8-right-26.png`), followed by `goto_frame_spin` (a plain
  `QSpinBox` — type a frame number and press Enter/lose focus to jump
  there via `_on_goto_frame_entered` -> `_jump_to_frame`; range kept in
  sync with the clip length from `_on_duration_changed`, value kept in
  sync with playback from `_on_position_changed`, both via `blockSignals`
  so neither echoes back as a fresh `editingFinished` jump), `fps_label`
  (read-only "N FPS" info — `self._fps_value` is the actual source of
  truth `_fps()` returns, no widget backing it), a 0.01x-1.00x
  `speed_slider` -> `self.player.setPlaybackRate` (a slow-motion control,
  deliberately capped at normal speed), then `edit_comment_button`
  (`icons8-edit-50.png`, fixed `_EDIT_COMMENT_BUTTON_SIZE`x
  `_EDIT_COMMENT_BUTTON_SIZE` = 32x32 square — disabled until `load_video`
  is called, emits `editCommentRequested` for the host page to open
  BananaSketch) and `send_discord_button` (added 2026-08-08 for the "Send
  to Discord" feature — no icon file placed yet, so `_set_button_icon`
  shows a plain "Discord" text label, same fallback every other button
  here used before its own icon arrived — points at `_DISCORD_ICON_PATH`
  for whenever `icons8-discord-50.png` is added to `../images/`), same
  enabled-only-while-a-video-is-loaded lifecycle as `edit_comment_button`
  (both toggled together in `load_video`/`clear_video`), emitting
  `sendToDiscordRequested` rather than opening anything itself —
  `video_library_page.py`'s `_on_send_discord_clicked` owns the actual
  send (reading the configured channel/token, running
  `discord_send_worker.py`'s `DiscordSendWorker`), the same
  signal-out/host-page-does-the-work split `edit_comment_button` uses.
  `position_slider` (a plain `QSlider`, moved into its own `slider_row`
  with `start_frame_label`/`end_frame_label`) used to be a
  `_CommentAwareSlider` with tick marks at commented frames — reverted to
  a plain slider 2026-08-08, since this plugin no longer reads comment
  data at all. Frame indexing is `round(position_ms / 1000 * fps)` —
  `QMediaPlayer` has no native frame-accurate seek for arbitrary
  containers, a deliberate approximation.

  Keyboard shortcuts (all `QShortcut`, context
  `Qt.WidgetWithChildrenShortcut` so each only fires while this widget or
  a child has focus, all routed through `_is_typing()` so typing into
  `goto_frame_spin`'s internal line edit never hijacks the cursor): "A"/"D"
  step one frame back/forward, "Space" toggles play/pause. Comment-jump
  ("Shift+A"/"Shift+D") and draw-tool shortcuts (Ctrl+Z, "1"-"4") are gone
  — moved to BananaSketch's `editor_widget.py` along with everything else
  they used to act on.

  `_set_button_icon` is a `@staticmethod` — used throughout this file for
  every icon button, and reused directly by `video_library_page.py`'s
  sort/view buttons (`PlayerWidget._set_button_icon(...)`).
- `discord_send_worker.py` — `DiscordSendWorker`, added 2026-08-08 for the
  "Send to Discord" button (`player_widget.py`'s `send_discord_button`
  above). One-shot `QThread` subclass, same shape as
  `plugins/core/submit/status_worker.py`'s `RepoStatusWorker`: takes the
  (already-validated) forum channel id + the video's own shot title rather
  than an already-resolved thread id, plus `max_upload_bytes`/
  `ffmpeg_path` (keyword-only). Its `run()` chains three blocking network/
  subprocess calls off the UI thread: `../core/video_compress.py`'s
  `compress_to_fit` (only actually transcodes when the video's already
  over `max_upload_bytes` — a temp dir it always cleans up in a `finally`,
  whether the send succeeds or fails afterward), then
  `../core/discord_client.py`'s `find_or_create_forum_post`, then
  `send_video` — emitting `succeeded` or `failed(message)`.
  `video_library_page.py`'s `_on_send_discord_clicked` constructs and
  starts one per click — first resolving the selected video's shot code
  via `_parsed_by_video` (warning instead if it doesn't parse, since Send
  to Discord has no shot code to find/create a post with then), validating
  a forum channel id + bot token are configured, and reading Max Upload
  Size/ffmpeg Path (see `repo_video_settings_page.py`'s Discord section
  above) — before ever spinning up the thread — disabling
  `send_discord_button` for the duration and re-enabling it from whichever
  signal fires back.

**Working here:** stay inside `interface/` unless the change needs a new
top-level `core/` primitive (the app's own) or a `../core/` addition this
UI depends on. If the task is actually about drawing/commenting on a
frame, it's `cache/plugins/BananaSketch/interface/` you want, not here —
see that plugin's own README. The `_VideoCard` QSS rules
(`QFrame#videoCard`) were added to the app's top-level `core/theme.py`'s
shared stylesheet rather than a local `setStyleSheet` — that's the "needs
a new `core/` primitive" exception.

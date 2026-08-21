# cache/plugins/UkoreShot/core/

Non-UI logic for the UkoreShot plugin — no PySide6 imports in this folder
at all. Split out from the plugin's flat file layout on 2026-07-21 so a
session working on data/path/persistence concerns doesn't need to open any
of `interface/`'s widget code (and vice versa) — see the top-level
`../README.md`'s "Structure" section for the full rule.

**Naming note:** this plugin no longer has any direct `from core.xxx import
yyy` imports at all — `comment_store.py`'s old `from core.store import
LocalConfigStore` (ported unmodified from pre-2026-08-08 git history) was
found broken 2026-08-21 (`No module named 'core.store'` — that module no
longer exists in the app's current `core/`, per `PluginLoader`'s own error
log) and fixed to take `api` and use the documented `api.local_config`
instead, the same fix direction `share_sync.py` (new code) already used for
`ConflictError` via `plugin_api`. If a future change here is tempted to
write `from core.xxx import yyy` again: don't — go through `plugin_api`
(`plugin-api.md`'s own rule) or thread `api` through instead, exactly
because the app's own `core/` layout isn't a stable contract this plugin
can assume across app versions, as this incident showed. (`comment_store.py`
was briefly gone from this folder entirely — moved to
`cache/plugins/BananaSketch/core/` on 2026-08-08 along with the rest of the
draw/comment editor — but came back here 2026-08-20 when that editor was
revived in-house; see `../interface/README.md`.)

## Files

- `video_path_store.py` — `resolve_video_root(api, project_id, repo_id)`:
  a fixed per-machine folder under `api.cache_dir / "ukore_shot" /
  project_id / repo_id`, created (`mkdir(parents=True, exist_ok=True)`) on
  every resolution. As of the cache-folder move, UkoreShot's playblast
  library is entirely local to each machine — it lives under UkoreHub's
  own gitignored `cache/` dir (see root `CLAUDE.md`'s "Program folder
  stays program-only"), never inside the repo checkout, so it's never
  synced via git/repo pull or push and never shared across machines. There
  is no longer a studio-admin picker for this (the old Repository Setting
  > UkoreShot Custom Path list is gone — see
  `../interface/repo_video_settings_page.py`, now purely informational).
  `../maya-scripts/UkorePlayblast/function.py`'s `_resolve_video_root`
  mirrors this exactly via `PublishApi.repo_paths.find_cache_dir()` (no
  shared bridge file needed — both sides just agree on the same
  `cache_dir/ukore_shot/<project_id>/<repo_id>` folder) — see
  `../maya-scripts/README.md`.
- `video_naming.py` — `parse_video_filename(video_path) -> dict | None`:
  the desktop-side reader of `../maya-scripts/UkorePlayblast/`'s flat
  `SEQ_ShotCode_Variation_index_version.ext` naming convention (see
  `../maya-scripts/README.md` for the full scheme) — `_FILENAME_PATTERN` mirrors
  `function.py`'s own pattern exactly (duplicated for the same "Maya's
  Python can't import this desktop-side package" reason every other
  cross-environment duplication in this codebase exists for). Returns
  `None` for anything that doesn't match — a pre-2026-07-20
  shot/version-subfoldered playblast, or any unrelated file — which
  `../interface/video_library_page.py` still lists (Name column shows the
  raw stem either way), it just can't get a generated share code (Mark as
  Share needs a parsed shot_code/version).
- `video_sequence.py` — added 2026-08-20: `ensure_sequence(ffmpeg_path,
  video_path)` lazily extracts a video into a numbered PNG sequence via
  `ffmpeg -vsync 0` (no-op if `has_sequence()` already finds one — whether
  from a prior extraction or an old, pre-2026-08-20 Maya-written one, see
  `../maya-scripts/README.md`), writing into `sequence_dir_for(video_path)`
  (`video_path.parent / video_path.stem` — the same `<stem>/` folder
  UkorePlayblast itself used to write into). Reuses
  `video_compress.py::resolve_ffmpeg_path` directly. **Only ever called
  from two explicit user actions** in `video_library_page.py`
  (`pushButton_comment`/`pushButton_mark_as_share`'s handlers) — never from
  a reload/refresh scan, confirmed with the user specifically so casually
  browsing the library never costs an ffmpeg call. `probe_fps` parses the
  " fps," token off ffmpeg's own `-i`-with-no-output stderr dump, same
  technique `video_compress.py`'s `_probe_duration_seconds` already uses
  for that stream's `Duration:` line. `AUDIO_FILENAME_SUFFIX`/
  `audio_path_for`/`has_audio_file`/`extract_audio` (added 2026-08-21) do
  the same job for a video's audio track: `ensure_sequence` calls
  `extract_audio` right after the frame extraction, writing
  `<stem>.audio.m4a` (re-encoded to AAC, not stream-copied, so every source
  codec lands in one predictable container `../interface/sequence_player.py`
  can always play back) into the same `sequence_dir` the frames live in — a
  silent source video (most playblasts) just means no file gets written,
  not an error; `has_audio_file` is what lets a later call skip
  re-extracting once it exists (a silent video has no persisted "already
  checked" marker, so it cheaply re-attempts the ffmpeg probe on every
  call — an accepted trade-off, same one `probe_fps` already makes for
  fps).
- `comment_store.py` — per-video comment/drawing/share metadata, revived
  2026-08-20 from git history (commit `f9b3505` in `UkoreHubDev`, before
  `BananaSketch`'s extraction — see `../interface/README.md`). Now lives at
  `<sequence_dir>/comments.json` (a `video_sequence.py` extraction folder),
  not a `<video>.ukoreshot.json` sidecar next to the video file — matches
  the user's instruction that comment data lives in the same folder as the
  image sequence. `load`/`save` round-trip `{"frames": {...}, "share":
  {...}}`; `get_share_state`/`set_share_state` manage the `"share"` block
  (`is_shared`, `code`, `shared_at`, `frame_count`, `image_format`, `fps`,
  `audio_format` — the last added 2026-08-21, `None` for a silent video,
  `"m4a"` once `video_sequence.extract_audio` has produced one — enough
  for `share_sync.py` to reconstruct every blob name for a share
  without ever needing to *list* a bucket). `generate_share_code(shot_code,
  version)` builds the `{shot_code}_v{version:03d}_{4 hex chars}` label
  Mark as Share generates once and Copy Clipboard copies as plain text.
- `share_sync.py` — background R2 push/pull for a shared video, added
  2026-08-20. `api.cloud_sync` (an already-built `R2JsonSync | None`) is
  the only sanctioned way a plugin touches R2 — see `plugin-api.md`'s "What's
  deliberately not re-exported" section; `core.vcs.cloud_sync.R2JsonSync`
  itself is never imported here. Since `R2JsonSync` has no "list objects"
  operation, a share code needs a small pointer blob
  (`ukore_shot/share_codes/<code>.json`, `push_pointer`/`pull_pointer`) to
  be resolvable at all — it records exactly which
  project/repo/stem/frame_count/image_format/fps a code maps to, so a
  puller can reconstruct every frame's exact blob name
  (`{stem}.{i:05d}.{image_format}`) without ever enumerating the bucket.
  Three one-shot `QThread`s, same shape as `discord_send_worker.py`'s
  `DiscordSendWorker`: `ShareUploadWorker` (every file in `sequence_dir` —
  every frame + `comments.json`, and, since 2026-08-21, the extracted
  `<stem>.audio.m4a` too whenever one exists, since this worker just pushes
  whatever's actually sitting in the folder rather than an enumerated file
  list, for Mark as Share), `CommentSyncWorker` (just `comments.json`, for
  `comment_editor.py`'s Save Comment on an already-shared video — the
  incremental-sync-on-save behavior confirmed with the user), and
  `PullByCodeWorker` (resolves a pasted code, pulls every frame +
  `comments.json`, then — best-effort, a failure here doesn't fail the
  whole pull — the audio track too if the pointer's `audio_format` says one
  was pushed, down into `video_root/<stem>/` —
  `video_library_page.py`'s search-bar Enter-key round-trip). `push_pointer`'s
  `audio_format` param (added 2026-08-21, defaults to `None` for the common
  silent-video case) is what tells a puller on a different machine whether
  to bother asking for that blob at all — an older pointer pushed before
  audio support existed simply has no such key, `pointer.get("audio_format")`
  returns `None`, nothing extra is attempted. All three swallow
  `ConflictError` per-file — last-write-wins is correct for a shared asset,
  same reasoning `launcher.py::_push_asset` already documents for
  thumbnails/program_icons.
- `discord_client.py` — the Discord side of the "Send to Discord" button
  (`../interface/player_widget.py`'s `send_discord_button`,
  `sendToDiscordRequested`). `get_channel_id`/`set_channel_id` and
  `get_bot_token`/`set_bot_token` all read/write the same per-repo,
  studio-shared `"ukore_shot"` `PluginConfigStore` `video_path_store.py`
  already uses (`discord_channel_id`/`discord_bot_token` keys,
  `_repo_key`-namespaced the same way as `video_path_store.py`'s own
  `repo_video_custom_path`). **The bot token is stored here too, as of
  2026-08-08** — a deliberate, explicitly-confirmed-with-the-user tradeoff,
  not an oversight: an earlier revision kept it out of git via the OS
  keyring (mirroring `core/github/token_store.py`'s pattern, the app's own
  top-level `core/`), but the user asked for every machine to pick up the
  same token automatically via git instead, which means it's committed to
  the studio repo in **plain text and stays in git history forever** —
  repo access is effectively bot access. See the function's own docstring.
  `find_or_create_forum_post(token, forum_channel_id, title)` resolves a
  shot code (the video's own shot code, from `video_naming.py` — the
  channel configured here must be a **Forum Channel**, not a plain text
  one) to the id of the matching forum post (a Discord Thread): reuses one
  with that exact name if it finds one among both active and archived
  threads, otherwise creates a new one whose starter message is a
  placeholder embed **authored by the bot** — that detail matters beyond
  just cosmetics, since `Jacobot`'s (`cache/plugins/Jacobot/`, a separate
  always-on service — see its own README) `/setdesc`/`/settitle`/
  `/thumbnail` slash commands can only edit a starter message the bot
  itself created (Discord only lets a message's own author edit it).
  `send_video(token, channel_id, video_path, message)` then POSTs the
  video as a file attachment to that resolved thread (a Discord thread
  accepts the same `/channels/{id}/messages` endpoint a normal channel
  does), hand-building the multipart/form-data body via stdlib `urllib`
  (same "no `requests` dependency" convention `core/github/commits_api.py`
  already uses) since this app has no multipart helper. Both functions
  raise `DiscordApiError` with an already user-safe message for every
  failure mode (bad token, missing permission, unknown channel, file over
  Discord's ~10MB default limit, or a plain network error) —
  `../interface/video_library_page.py`'s `_on_discord_send_failed` just
  shows it directly in a `QMessageBox`. `get_max_upload_mb`/
  `set_max_upload_mb` (per-repo, shared, `discord_max_upload_mb` key,
  defaults to `DEFAULT_MAX_UPLOAD_MB`) and `get_ffmpeg_path`/
  `set_ffmpeg_path` (**per-machine**, `shared=False` — unlike everything
  else here, an ffmpeg install path only makes sense per machine) back the
  compression step in `video_compress.py` below.
- `video_compress.py` — added 2026-08-08 after a real "video too large"
  `DiscordApiError` (HTTP 413) report: `compress_to_fit(ffmpeg_path,
  video_path, max_bytes)` shells out to `ffmpeg` (via `subprocess`, no
  Python video library dependency) to transcode a video down to a temp
  `.mp4` under `max_bytes`, returning `video_path` unchanged if it's
  already small enough (no transcode at all). Single-pass, bitrate
  calculated from the video's own duration (read off ffmpeg's own stderr
  "Duration:" line via `-i` with no output — avoids needing a separate
  `ffprobe` lookup) with a 5% safety margin and a fixed 128kbps reserved
  for audio — deliberately not a frame-accurate two-pass encode, which
  would roughly double encode time for a use case that only needs to land
  under a size cap, not hit it exactly. `resolve_ffmpeg_path(configured_path,
  cache_dir)` prefers an explicit configured path
  (`discord_client.get_ffmpeg_path`), then a previously-downloaded copy
  already cached at `cache_dir/ukore_shot/bin/ffmpeg.exe`, then downloads
  that same win64 GPL build fresh into that cache path (Windows only —
  `_download_ffmpeg`, from `github.com/BtbN/FFmpeg-Builds`'s `latest`
  release asset), then falls back to a PATH lookup, raising
  `VideoCompressionError` immediately if none of these resolves — same
  "explicit per-machine override, else built-in, else PATH lookup" shape
  `plugins/core/software_linker/` already uses for `maya.exe`, just with an
  on-demand download as the new middle tier (added specifically so a fresh
  machine never blocks on "ffmpeg Required" for its first Comment/Mark as
  Share/Discord send — this function is also `video_sequence.py`'s only
  ffmpeg resolution path, see that entry above). A git-tracked
  `bin/ffmpeg.exe` shipped directly in this repo for one day (2026-08-21)
  before being removed — 145MB blew past GitHub's 100MB file-size limit
  and blocked `git push` outright; the cache-dir download replaces it
  entirely, so nothing about ffmpeg lives in this repo's git history
  anymore. `../interface/discord_send_worker.py`
  is the only Discord-side caller:
  compresses (into a fresh temp dir it cleans up afterward, success or
  failure) only when the video's already over the configured limit, then
  sends whichever path (original or compressed) actually ends up under it.

**Working here:** stay inside `core/` unless the change needs a new
top-level `core/` primitive (a genuinely different package, see the naming
note above) or touches `../maya-scripts/UkorePlayblast/`'s matching
`_resolve_video_root` (read-only from this side — both just happen to
agree on the same `cache_dir`-derived folder, see `../maya-scripts/README.md`).

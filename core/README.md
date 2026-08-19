# cache/plugins/UkoreShot/core/

Non-UI logic for the UkoreShot plugin — no PySide6 imports in this folder
at all. Split out from the plugin's flat file layout on 2026-07-21 so a
session working on data/path/persistence concerns doesn't need to open any
of `interface/`'s widget code (and vice versa) — see the top-level
`../README.md`'s "Structure" section for the full rule.

**Naming note:** `video_path_store.py`'s `from core.exceptions import
NotFoundError` always means the app's own **top-level** `core/` package
(`C:\Tonmai\UkoreHub\core\`), never this folder — Python resolves
`from core...` as an absolute import from the repo root on `sys.path`,
completely independent of where the importing file itself lives. The two
packages share a name by coincidence, not relationship; don't assume a
bare `core.something` import anywhere in this plugin means
`ukoreshot_plugin.core`. (This folder used to have a second example,
`comment_store.py`'s `from core.store import LocalConfigStore` — that
file moved to `cache/plugins/BananaSketch/core/` 2026-08-08 along with the
rest of the draw/comment editor, and its own README carries the same note
forward.)

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
  `cache/plugins/UkorePlayblast/`'s Maya-side `function.py`'s
  `_resolve_video_root` mirrors this exactly via
  `PublishApi.repo_paths.find_cache_dir()` (no shared bridge file needed —
  both sides just agree on the same `cache_dir/ukore_shot/<project_id>/
  <repo_id>` folder) — see that plugin's README.
- `video_naming.py` — `parse_video_filename(video_path) -> dict | None`:
  the desktop-side reader of UkorePlayblast's flat
  `SEQ_ShotCode_Variation_index_version.ext` naming convention (see that
  plugin's README for the full scheme) — `_FILENAME_PATTERN` mirrors
  `function.py`'s own pattern exactly (duplicated for the same "Maya's
  Python can't import this desktop-side package" reason every other
  cross-environment duplication in this codebase exists for). Returns
  `None` for anything that doesn't match — a pre-2026-07-20
  shot/version-subfoldered playblast, or any unrelated file — which
  `../interface/video_library_page.py`/`filter_sidebar.py` treat as
  "Unknown" rather than an error or something to hide.
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
  under a size cap, not hit it exactly. `resolve_ffmpeg_path` prefers an
  explicit configured path (`discord_client.get_ffmpeg_path`) and falls
  back to a PATH lookup, raising `VideoCompressionError` immediately if
  neither resolves — same "explicit per-machine override, else PATH
  lookup" shape `plugins/core/software_linker/` already uses for
  `maya.exe`. `../interface/discord_send_worker.py` is the only caller:
  compresses (into a fresh temp dir it cleans up afterward, success or
  failure) only when the video's already over the configured limit, then
  sends whichever path (original or compressed) actually ends up under it.

**Working here:** stay inside `core/` unless the change needs a new
top-level `core/` primitive (a genuinely different package, see the naming
note above) or touches `cache/plugins/UkorePlayblast/`'s matching
`_resolve_video_root` (read-only from this plugin's side — both just
happen to agree on the same `cache_dir`-derived folder, see that plugin's
own README).

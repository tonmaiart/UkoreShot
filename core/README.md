# cache/plugins/UkoreShot/core/

Non-UI logic for the UkoreShot plugin — no PySide6 imports in this folder
at all. Split out from the plugin's flat file layout on 2026-07-21 so a
session working on data/path/persistence concerns doesn't need to open any
of `interface/`'s widget code (and vice versa) — see the top-level
`../README.md`'s "Structure" section for the full rule.

**Naming note:** `from core.X import Y` inside these files (e.g.
`comment_store.py`'s `from core.store import LocalConfigStore`,
`draw_overlay.py`'s `from core.extensibility import debug_log` in
`../interface/`) always means the app's own **top-level** `core/` package
(`C:\Tonmai\UkoreHub\core\`), never this folder — Python resolves
`from core...` as an absolute import from the repo root on `sys.path`,
completely independent of where the importing file itself lives. The two
packages share a name by coincidence, not relationship; don't assume a
bare `core.something` import anywhere in this plugin means
`ukoreshot_plugin.core`.

## Files

- `video_path_store.py` — reads the active repo's Custom Paths off
  `project_editor`'s store, stores UkoreShot's own chosen `custom_path_id`
  in its own `PluginConfigStore` (`data/plugins/core/ukore_shot.json`,
  `repo_video_custom_path: {"<project_id>:<repo_id>": "<custom_path_id>"}`),
  and `resolve_video_root(api, project_id, repo_id)` — joins
  `workspace_root / repo.local_path / custom_path.path` into the actual
  absolute folder.

  UkoreShot does **not** own its own free-text folder setting — instead,
  Repository Setting > UkoreShot (`../interface/repo_video_settings_page.py`)
  lets a studio admin pick one of the active repo's own already-declared
  **Custom Paths** (Repository Setting > Custom Paths > Create Input Path,
  owned by `plugins/core/project_editor/` — see that plugin's README) as
  this repo's playblast video root. Read directly off `project_editor`'s
  shared `PluginConfigStore` (`data/plugins/core/project_editor.json`),
  the "convention, not import" pattern every cross-plugin read in this app
  uses — see `plugins/README.md`'s "Sharing data with another plugin".
  Auto-uses the repo's only declared Custom Path if there's exactly one (no
  explicit choice needed); requires an explicit pick if there's more than
  one; resolves to nothing if there are none yet. `resolve_video_root`'s
  resolution order mirrors `PublishApi.repo_paths.get_publish_root`'s own
  convention. `plugins/repo_internal/UkorePlayblast/`'s Maya-side `function.py`
  reads UkoreShot's own choice the same "construct the store straight off
  disk" way (no shared bridge file needed) — see that plugin's README.
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
- `comment_store.py` — sidecar JSON persistence, one file per video:
  `<video_path>.ukoreshot.json` next to the video itself (travels with the
  shared library folder, no separate app data store). Shape:
  `{"frames": {"<frame_index>": {"strokes": [{"color", "width", "points"}], "comments": [{"id", "author", "text", "timestamp"}], "text_boxes": [{"text", "x", "y"}]}}}`,
  points/positions stored normalized 0-1 in widget space. `"comments"`
  (added 2026-07-20) replaces the older single `"note"` string a frame
  saved before that date may still have — `../interface/player_widget.py`'s
  `PlayerWidget._migrate_comments` handles reading the old shape;
  `comment_store.py` itself doesn't rewrite existing files, only future
  saves. `current_username()` (also added 2026-07-20) is the shared
  "who's commenting" lookup `../interface/comment_thread.py` calls for
  every new comment: the cached `LocalConfigStore.github_username` if this
  machine has logged in via GitHub, else `getpass.getuser()` — no live
  network call, since a comment shouldn't block on one.
  `list_commenters(video_path)` (also added 2026-07-20) is every distinct
  comment author across a video's frames — `video_library_page.py` caches
  this per video for `filter_sidebar.py`'s "Commented By" filter category;
  a frame that only has the legacy `"note"` string contributes nothing (no
  author was ever recorded for it). `_REPO_ROOT` is
  `Path(__file__).resolve().parents[4]` — four parents up from
  `core/comment_store.py` is the UkoreHub repo root (no `api` handle
  available this deep to resolve it any other way).

- `discord_client.py` — the Discord side of the "Send to Discord" button
  (`../interface/player_widget.py`'s `send_discord_button`,
  `sendToDiscordRequested`). `get_channel_id`/`set_channel_id` read/write a
  per-repo, studio-shared Discord channel id off the same `"ukore_shot"`
  `PluginConfigStore` `video_path_store.py` already uses (a new
  `discord_channel_id` key, `_repo_key`-namespaced the same way) — not a
  secret, so it's fine in the shared/git-tracked store, unlike the bot
  token below. `send_video(token, channel_id, video_path, message)` POSTs
  the video as a file attachment to Discord's bot REST API
  (`/channels/{id}/messages`), hand-building the multipart/form-data body
  via stdlib `urllib` (same "no `requests` dependency" convention
  `core/github/commits_api.py` already uses) since this app has no
  multipart helper. Raises `DiscordApiError` with an already
  user-safe message for every failure mode (bad token, missing permission,
  unknown channel, file over Discord's ~10MB default limit, or a plain
  network error) — `../interface/video_library_page.py`'s
  `_on_discord_send_failed` just shows it directly in a `QMessageBox`.
- `discord_token_store.py` — the Discord bot token, **per-machine**, unlike
  the channel id above: every machine that should be able to use Send to
  Discord needs the same studio bot token entered once via Repository
  Setting > UkoreShot's Bot Token field. Same OS-keyring-first,
  gitignored-JSON-fallback shape as `core/github/token_store.py` (the
  app's top-level `core/`, its own established pattern for a real secret),
  just deliberately re-implemented as plain module functions here instead
  of a class — matching this folder's own `comment_store.py`/
  `video_path_store.py` style (module functions, not classes needing
  manual construction) rather than importing across to `core/github/`,
  which has its own unrelated `SERVICE_NAME`/`KEYRING_USERNAME_KEY`
  constants scoped to GitHub specifically. Fallback file lives under
  `data/plugins/local/` (already wholesale-gitignored — see root
  `.gitignore` — so it needed no new entry).

**Working here:** stay inside `core/` unless the change needs a new
top-level `core/` primitive (a genuinely different package, see the naming
note above) or touches `plugins/core/project_editor/`'s Custom Paths
data shape / `plugins/repo_internal/UkorePlayblast/`'s output (both read-only,
via the conventions documented above).

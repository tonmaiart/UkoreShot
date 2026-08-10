from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_API_BASE = "https://discord.com/api/v10"
_CHANNEL_ID_KEY = "discord_channel_id"
_BOT_TOKEN_KEY = "discord_bot_token"
_MAX_UPLOAD_MB_KEY = "discord_max_upload_mb"
_FFMPEG_PATH_KEY = "ffmpeg_path"
DEFAULT_MAX_UPLOAD_MB = 10
# Mirrors Jacobot's own forum_post.py copy of the same string — the two
# can't share a Python import (separate repos/processes), so this is kept
# in sync by hand, same "duplicated deliberately" convention this codebase
# already uses for the UkoreShot/UkorePlayblast naming regex.
_PLACEHOLDER_DESCRIPTION = "*No description yet — use `/setdesc`.*"


class DiscordApiError(Exception):
    """Raised with a message that's already safe to show the user — never
    lets urllib's own exception types (or a raw Discord error payload)
    surface past send_video."""


def _repo_key(project_id: str, repo_id: str) -> str:
    return f"{project_id}:{repo_id}"


def get_channel_id(api, project_id: str, repo_id: str) -> str | None:
    """Per-repo, shared across the studio (not a secret — see
    ../README.md) — read off the same "ukore_shot" PluginConfigStore
    video_path_store.py already uses for its own custom-path selection."""
    store = api.plugin_config_store("ukore_shot", shared=True)
    return store.get(_CHANNEL_ID_KEY, {}).get(_repo_key(project_id, repo_id))


def set_channel_id(api, project_id: str, repo_id: str, channel_id: str | None) -> None:
    store = api.plugin_config_store("ukore_shot", shared=True)
    channel_ids = store.get(_CHANNEL_ID_KEY, {})
    key = _repo_key(project_id, repo_id)
    if not channel_id:
        channel_ids.pop(key, None)
    else:
        channel_ids[key] = channel_id
    store.set(_CHANNEL_ID_KEY, channel_ids)


def get_bot_token(api, project_id: str, repo_id: str) -> str | None:
    """Per-repo, shared across the studio — same store/shape as
    get_channel_id above. NOT stored via the OS keyring (an earlier
    revision of this feature did) — confirmed with the user 2026-08-08 that
    the token should live here instead so every machine picks it up
    automatically via git, trading that convenience for a real security
    cost: this value is committed to the studio repo in plain text and
    stays in git history forever, so anyone with read access to this repo
    (now or via history, at any point in the future) can extract it and
    fully control the bot. Only use a bot whose access you're fine with the
    whole studio effectively having."""
    store = api.plugin_config_store("ukore_shot", shared=True)
    return store.get(_BOT_TOKEN_KEY, {}).get(_repo_key(project_id, repo_id))


def set_bot_token(api, project_id: str, repo_id: str, token: str | None) -> None:
    store = api.plugin_config_store("ukore_shot", shared=True)
    tokens = store.get(_BOT_TOKEN_KEY, {})
    key = _repo_key(project_id, repo_id)
    if not token:
        tokens.pop(key, None)
    else:
        tokens[key] = token
    store.set(_BOT_TOKEN_KEY, tokens)


def get_max_upload_mb(api, project_id: str, repo_id: str) -> int:
    """Per-repo, shared across the studio — same store/shape as
    get_channel_id above. Defaults to Discord's own un-boosted limit;
    raise it here if the studio's server is boosted enough to allow
    bigger uploads, so send_discord_worker.py compresses less
    aggressively (or not at all)."""
    store = api.plugin_config_store("ukore_shot", shared=True)
    return store.get(_MAX_UPLOAD_MB_KEY, {}).get(_repo_key(project_id, repo_id), DEFAULT_MAX_UPLOAD_MB)


def set_max_upload_mb(api, project_id: str, repo_id: str, max_upload_mb: int | None) -> None:
    store = api.plugin_config_store("ukore_shot", shared=True)
    values = store.get(_MAX_UPLOAD_MB_KEY, {})
    key = _repo_key(project_id, repo_id)
    if not max_upload_mb:
        values.pop(key, None)
    else:
        values[key] = max_upload_mb
    store.set(_MAX_UPLOAD_MB_KEY, values)


def get_ffmpeg_path(api) -> str | None:
    """Per-machine (shared=False, gitignored) — same "explicit path,
    per-machine" convention plugins/core/software_linker/ uses for
    maya.exe. None/blank means "look up ffmpeg on this machine's PATH",
    see video_compress.py's resolve_ffmpeg_path."""
    store = api.plugin_config_store("ukore_shot", shared=False)
    return store.get(_FFMPEG_PATH_KEY) or None


def set_ffmpeg_path(api, ffmpeg_path: str | None) -> None:
    store = api.plugin_config_store("ukore_shot", shared=False)
    store.set(_FFMPEG_PATH_KEY, ffmpeg_path or None)


def _request_json(token: str, url: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bot {token}", "User-Agent": "UkoreHub"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DiscordApiError(_describe_http_error(exc)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise DiscordApiError(f"Could not reach Discord: {reason}") from exc


def find_or_create_forum_post(token: str, forum_channel_id: str, title: str) -> str:
    """Resolves `title` (the shot code, e.g. "KBA030") to the id of the
    matching forum post (a Discord Thread) inside forum_channel_id —
    Repository Setting > UkoreShot's Channel ID field must point at a Forum
    Channel, not a plain text channel, for this to make sense. Reuses an
    existing post with that exact name if one exists (checking both active
    and archived threads, so a post that went quiet still gets reused
    rather than duplicated), otherwise creates a new one. The new post's
    starter message is authored by the bot itself with a placeholder
    embed — Jacobot's /setdesc, /setthumbnail, /settitle slash commands can
    only edit a starter message the bot itself created (Discord only lets a
    message's own author edit it), so every post this app creates has to
    start this way for those commands to work on it later. Returns the
    thread id, which send_video can be pointed at exactly like a normal
    channel id (Discord threads accept the same /channels/{id}/messages
    endpoint a regular channel does)."""
    guild_id = _request_json(token, f"{_API_BASE}/channels/{forum_channel_id}")["guild_id"]
    active = _request_json(token, f"{_API_BASE}/guilds/{guild_id}/threads/active")["threads"]
    archived = _request_json(token, f"{_API_BASE}/channels/{forum_channel_id}/threads/archived/public")["threads"]
    for thread in [t for t in active if t.get("parent_id") == forum_channel_id] + archived:
        if thread.get("name") == title:
            return thread["id"]

    created = _request_json(
        token,
        f"{_API_BASE}/channels/{forum_channel_id}/threads",
        method="POST",
        body={
            "name": title,
            "message": {"embeds": [{"title": title, "description": _PLACEHOLDER_DESCRIPTION}]},
        },
    )
    return created["id"]


def send_video(token: str, channel_id: str, video_path: Path, message: str) -> None:
    """Posts video_path as a file attachment to the given Discord channel via
    the bot REST API (POST /channels/{id}/messages, Authorization: Bot
    <token>). Builds the multipart/form-data body by hand via urllib
    (stdlib-only, same "no requests dependency" convention
    core/github/commits_api.py already uses) since there's no separate
    multipart helper in this codebase."""
    boundary = uuid.uuid4().hex
    payload = json.dumps({"content": message}).encode("utf-8")
    content_type = mimetypes.guess_type(video_path.name)[0] or "application/octet-stream"
    video_bytes = video_path.read_bytes()

    body = bytearray()

    def add_field(name: str, content: bytes, *, filename: str | None = None, field_content_type: str | None = None):
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        body.extend((disposition + "\r\n").encode("utf-8"))
        if field_content_type:
            body.extend(f"Content-Type: {field_content_type}\r\n".encode("utf-8"))
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    add_field("payload_json", payload, field_content_type="application/json")
    add_field("files[0]", video_bytes, filename=video_path.name, field_content_type=content_type)
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = urllib.request.Request(
        f"{_API_BASE}/channels/{channel_id}/messages",
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "UkoreHub",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        raise DiscordApiError(_describe_http_error(exc)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise DiscordApiError(f"Could not reach Discord: {reason}") from exc


def _describe_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(exc.read().decode("utf-8")).get("message", "")
    except Exception:
        detail = ""
    if exc.code == 401:
        return "Discord rejected the bot token (invalid or revoked) — re-enter it in Repository Setting > UkoreShot."
    if exc.code == 403:
        return (
            "The bot doesn't have permission to post in this channel — check it was invited with "
            "Send Messages/Attach Files and has access to the channel."
        )
    if exc.code == 404:
        return "Discord forum channel not found — check the Channel ID in Repository Setting > UkoreShot."
    if exc.code == 413:
        return "This video is too large for Discord to accept (default limit is 10MB unless the server is boosted)."
    return f"Discord rejected the request (HTTP {exc.code}){f': {detail}' if detail else ''}."

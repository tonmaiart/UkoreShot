from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_API_BASE = "https://discord.com/api/v10"
_CHANNEL_ID_KEY = "discord_channel_id"


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
        return "Discord channel not found — check the Channel ID in Repository Setting > UkoreShot."
    if exc.code == 413:
        return "This video is too large for Discord to accept (default limit is 10MB unless the server is boosted)."
    return f"Discord rejected the request (HTTP {exc.code}){f': {detail}' if detail else ''}."

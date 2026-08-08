from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE_NAME = "UkoreHub"
KEYRING_USERNAME_KEY = "ukoreshot_discord_bot_token"
# Four parents up from cache/plugins/UkoreShot/core/ is the UkoreHub
# repo root — same "no api handle available this deep" reasoning as
# comment_store.py's own _REPO_ROOT. data/plugins/local/ is already
# gitignored wholesale (see root .gitignore), so this fallback file needs no
# gitignore entry of its own.
_FALLBACK_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "plugins" / "local" / "ukoreshot_discord_token.json"
)


class DiscordTokenStoreFallbackUsed(Exception):
    """Raised (after the token is safely saved) to tell the UI to warn the user."""


def save_token(token: str) -> None:
    """Per-machine, not per-repo — the same bot token has to be entered on
    every machine that should be able to use Send to Discord (see
    ../README.md). Prefers the OS keyring (same core/github/token_store.py
    pattern this app already uses for the GitHub token), falling back to a
    gitignored local JSON file only if the keyring is unavailable."""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, KEYRING_USERNAME_KEY, token)
        if _FALLBACK_PATH.exists():
            _FALLBACK_PATH.unlink()
        return
    except Exception:
        _save_fallback(token)
        raise DiscordTokenStoreFallbackUsed(
            "Secure system keyring unavailable — storing your Discord bot token in a "
            f"local file at {_FALLBACK_PATH}. Do not share this file."
        )


def _save_fallback(token: str) -> None:
    _FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _FALLBACK_PATH.with_suffix(_FALLBACK_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"token": token}), encoding="utf-8")
    os.replace(tmp_path, _FALLBACK_PATH)


def load_token() -> str | None:
    try:
        import keyring

        token = keyring.get_password(SERVICE_NAME, KEYRING_USERNAME_KEY)
        if token:
            return token
    except Exception:
        pass
    if _FALLBACK_PATH.exists():
        data = json.loads(_FALLBACK_PATH.read_text(encoding="utf-8"))
        return data.get("token")
    return None


def clear_token() -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, KEYRING_USERNAME_KEY)
    except Exception:
        pass
    if _FALLBACK_PATH.exists():
        _FALLBACK_PATH.unlink()

"""ANTHROPIC_API_KEY lookup: OS keychain first, then environment, then a .env file.

The tool never asks for, reads, or stores your LinkedIn password.
"""

from __future__ import annotations

import os
from pathlib import Path

from linkreach import paths

SERVICE = "linkreach"
KEY_NAME = "ANTHROPIC_API_KEY"

_cached: str | None = None


def _from_keyring() -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, KEY_NAME) or None
    except Exception:  # no backend available, locked keychain, etc.
        return None


def _from_dotenv(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().removeprefix("export ").strip() == KEY_NAME:
            return v.strip().strip('"').strip("'") or None
    return None


def api_key() -> str | None:
    global _cached
    if _cached:
        return _cached
    key = (
        _from_keyring()
        or os.environ.get(KEY_NAME)
        or _from_dotenv(paths.inputs_dir() / ".env")
        or _from_dotenv(paths.home() / ".env")
    )
    _cached = key
    return key


def store_in_keyring(key: str) -> None:
    import keyring

    keyring.set_password(SERVICE, KEY_NAME, key)
    global _cached
    _cached = key


def reset_cache() -> None:
    global _cached
    _cached = None

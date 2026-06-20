"""Config helpers (reads config.toml + .env)."""

from __future__ import annotations

import os
from pathlib import Path

import tomllib

_CFG_PATH = Path(__file__).parent / "config.toml"


def _load() -> dict:
    if _CFG_PATH.exists():
        return tomllib.loads(_CFG_PATH.read_text())
    return {}


def tui_refresh_interval() -> int:
    return int(_load().get("tui", {}).get("refresh_interval", 60))


def poll_interval() -> int:
    return int(_load().get("monitor", {}).get("poll_interval", 300))


def api_key() -> str:
    return os.getenv("ZAI_API_KEY", "").strip()


def account_name() -> str:
    """Optional friendly label for the account (multi-account setups).

    Precedence: env ZAI_ACCOUNT_NAME > config.toml [zai].name.
    Returns "" if unset.
    """
    env_name = os.getenv("ZAI_ACCOUNT_NAME", "").strip()
    if env_name:
        return env_name
    return str(_load().get("zai", {}).get("name", "")).strip()

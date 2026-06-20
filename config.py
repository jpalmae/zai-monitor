"""Config helpers (reads config.toml + accounts.toml / .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tomllib
from dotenv import load_dotenv

load_dotenv()  # load .env so ZAI_API_KEY etc. are available to all consumers

_DIR = Path(__file__).parent
_CFG_PATH = _DIR / "config.toml"
_ACCOUNTS_PATH = _DIR / "accounts.toml"


@dataclass
class Account:
    """A single coding-plan subscription to monitor."""

    api_key: str
    name: str = ""
    # Per-account Telegram recipients. None/empty = use global [alerts] tg_chat_ids.
    tg_chat_ids: list[str] | None = None


def _load_cfg() -> dict:
    if _CFG_PATH.exists():
        return tomllib.loads(_CFG_PATH.read_text())
    return {}


def _load() -> dict:  # backward-compat alias
    return _load_cfg()


def tui_refresh_interval() -> int:
    return int(_load_cfg().get("tui", {}).get("refresh_interval", 60))


def poll_interval() -> int:
    return int(_load_cfg().get("monitor", {}).get("poll_interval", 300))


def api_key() -> str:
    return os.getenv("ZAI_API_KEY", "").strip()


def account_name() -> str:
    """Friendly label for the single-account (.env) path."""
    env_name = os.getenv("ZAI_ACCOUNT_NAME", "").strip()
    if env_name:
        return env_name
    return str(_load_cfg().get("zai", {}).get("name", "")).strip()


def load_accounts() -> list[Account]:
    """Return the list of accounts to monitor.

    Source (in order):
      1. accounts.toml -> [[accounts]] array (preferred for multi-account)
      2. fallback: a single account from .env ZAI_API_KEY (+ ZAI_ACCOUNT_NAME)
    """
    accounts: list[Account] = []
    if _ACCOUNTS_PATH.exists():
        data = tomllib.loads(_ACCOUNTS_PATH.read_text())
        for a in data.get("accounts", []):
            key = str(a.get("api_key", "")).strip()
            if not key:
                continue
            ids = None
            if isinstance(a.get("tg_chat_ids"), list):
                ids = [str(x) for x in a["tg_chat_ids"] if str(x).strip()]
            accounts.append(
                Account(api_key=key, name=str(a.get("name", "")).strip(), tg_chat_ids=ids)
            )
    if accounts:
        return accounts
    # fallback: single account from env
    key = os.getenv("ZAI_API_KEY", "").strip()
    if key:
        accounts.append(Account(api_key=key, name=account_name()))
    return accounts

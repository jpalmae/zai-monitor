"""Telegram setup helper.

Given a bot token (from @BotFather), waits for you to send /start to the bot,
auto-detects your chat_id, sends a test message, and writes both into config.toml.

Usage:
  python setup_telegram.py <BOT_TOKEN>
or set TG_BOT_TOKEN in .env and run:
  python setup_telegram.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).parent / "config.toml"


def _tg(token: str, method: str, data: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if data:
        req = urllib.request.Request(
            url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}
        )
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _set_config(token: str, chat_id: str) -> None:
    text = CONFIG.read_text() if CONFIG.exists() else ""
    text = re.sub(r'tg_bot_token\s*=\s*"[^"]*"', f'tg_bot_token = "{token}"', text)
    text = re.sub(r'tg_chat_id\s*=\s*"[^"]*"', f'tg_chat_id = "{chat_id}"', text)
    CONFIG.write_text(text)


def main() -> int:
    token = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not token:
        import os

        token = os.getenv("TG_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: pass bot token as arg or set TG_BOT_TOKEN.", file=sys.stderr)
        return 2

    # validate token
    try:
        me = _tg(token, "getMe")
    except Exception as e:
        print(f"ERROR: invalid token / network: {e}", file=sys.stderr)
        return 1
    botname = me.get("result", {}).get("username", "?")
    print(f"Bot OK: @{botname}")

    # baseline updates so we only react to NEW /start
    baseline = 0
    try:
        upd = _tg(token, "getUpdates", {"timeout": 0})
        result = upd.get("result", [])
        baseline = result[-1]["update_id"] if result else 0
    except Exception:
        pass

    print(f"\n>>> Open Telegram and send /start to @{botname}")
    print(">>> Waiting (up to 120s)...")

    deadline = time.time() + 120
    chat_id = None
    while time.time() < deadline:
        try:
            upd = _tg(token, "getUpdates", {"timeout": 5, "offset": baseline + 1})
            for u in upd.get("result", []):
                msg = u.get("message") or u.get("edited_message")
                if msg and msg.get("chat"):
                    chat_id = str(msg["chat"]["id"])
                    uname = msg["chat"].get("username") or msg["chat"].get("first_name", "")
                    print(f"    detected chat_id={chat_id} ({uname})")
                    break
        except Exception:
            pass
        if chat_id:
            break
        time.sleep(2)

    if not chat_id:
        print("Timed out waiting for /start. Re-run and send the message sooner.", file=sys.stderr)
        return 1

    # test message
    _tg(token, "sendMessage", {
        "chat_id": chat_id,
        "text": "✅ *zai-monitor* conectado. Recibirás alertas de cuota aquí.",
        "parse_mode": "Markdown",
    })
    print("    test message sent ✓")

    _set_config(token, chat_id)
    print(f"\nDONE. Wrote tg_bot_token & tg_chat_id into {CONFIG}")
    print("Restart the alerts daemon to pick up the change:")
    print("  launchctl unload ~/Library/LaunchAgents/ai.zai-monitor.alerts.plist")
    print("  launchctl load   ~/Library/LaunchAgents/ai.zai-monitor.alerts.plist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

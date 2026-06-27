"""Background alert daemon: poll quotas, fire Telegram alerts on thresholds.

Debounce model:
  - Each quota has a "cycle" = its current reset window (nextResetTime).
  - When usage crosses a threshold going UP, fire once per (quota, level, cycle).
  - When usage drops below `recovered_below` after a cycle change, fire a
    "recovered" notice and clear that quota's debounce so future alerts work.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import telegram

import store
from fetcher import Quota, Snapshot, ZaiError, fetch_snapshot

log = logging.getLogger("zai.alerts")

ORDER = ["five_hour", "weekly", "mcp"]


@dataclass
class AlertConfig:
    tg_bot_token: str
    tg_chat_ids: list[str]
    thresholds: list[int]
    recovered_below: int
    min_interval_sec: int


def _load_config() -> AlertConfig:
    from pathlib import Path

    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]

    cfg_path = Path(__file__).parent / "config.toml"
    cfg = tomllib.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    a = cfg.get("alerts", {})

    # chat ids: accept a list (tg_chat_ids) or a single/comma-separated value
    # (tg_chat_id) for backward compatibility.
    ids: list[str] = []
    if isinstance(a.get("tg_chat_ids"), list):
        ids = [str(x) for x in a["tg_chat_ids"] if str(x).strip()]
    single = str(a.get("tg_chat_id", "")).strip()
    if single:
        ids.extend([x.strip() for x in single.split(",") if x.strip()])
    # dedupe, preserve order
    seen: set[str] = set()
    chat_ids = [x for x in ids if not (x in seen or seen.add(x))]

    return AlertConfig(
        tg_bot_token=a.get("tg_bot_token", ""),
        tg_chat_ids=chat_ids,
        thresholds=sorted(a.get("thresholds", [50, 75, 90, 100])),
        recovered_below=int(a.get("recovered_below", 20)),
        min_interval_sec=int(a.get("min_interval_sec", 60)),
    )


def _fmt_quota(q: Quota) -> str:
    reset = q.next_reset
    if reset:

        local = reset.astimezone()
        reset_s = local.strftime("%a %H:%M")
    else:
        reset_s = "?"
    extra = ""
    if q.has_counts and q.used is not None and q.usage is not None:
        extra = f" ({q.used}/{q.usage} used)"
    return f"  • {q.title}: {q.percentage}%{extra} — resets {reset_s}"


def _build_message(
    account_name: str, snap: Snapshot, fired: list[tuple[str, int]], recovered: list[str]
) -> str:
    level = (snap.level or "?").upper()
    title = f"*z.ai Coding Plan ({level})* quota update"
    if account_name:
        title += f" — _{account_name}_"
    lines = [title, ""]
    if fired:
        parts = []
        for key, _lvl in fired:
            q = snap.get(key)
            if q:
                parts.append(f"{q.title} → *{q.percentage}%*")
        lines.append("⚠️ Threshold crossed: " + ", ".join(parts))
    if recovered:
        lines.append("✅ Reset/recovered: " + ", ".join(recovered))
    lines.append("")
    for key in ORDER:
        q = snap.get(key)
        if q:
            lines.append(_fmt_quota(q))
    return "\n".join(lines)


def _send_telegram(bot_token: str, chat_ids: list[str], text: str) -> bool:
    """Send text to all chat_ids. Returns True if sent (or skipped due to no config)."""
    if not bot_token or not chat_ids:
        log.info("telegram not configured; would send:\n%s", text)
        return True
    bot = telegram.Bot(token=bot_token)
    ok = False
    for chat_id in chat_ids:
        try:
            bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
            )
            log.info("sent to chat_id=%s", chat_id)
            ok = True
        except Exception as e:
            log.error("telegram send to %s failed: %s", chat_id, e)
    return ok


def evaluate(
    account: str, snap: Snapshot, cfg: AlertConfig
) -> tuple[list[tuple[str, int]], list[str], list[tuple[str, int, str]]]:
    """Decide what to fire WITHOUT marking. Returns (fired, recovered, to_mark).

    to_mark is a list of (quota_key, level, cycle_id) to persist AFTER a
    successful send, so failed sends can retry on the next poll.
    """
    fired: list[tuple[str, int]] = []
    recovered: list[str] = []
    to_mark: list[tuple[str, int, str]] = []

    for key in ORDER:
        q = snap.get(key)
        if not q:
            continue
        reset_ts = q.next_reset.timestamp() if q.next_reset else None
        next_cycle = store.cycle_id(reset_ts)
        store.record_history(account, key, q.percentage, reset_ts)

        # Recovered / reset notice: fire once per cycle when usage drops low.
        if q.percentage < cfg.recovered_below:
            should, _ = store.should_fire(account, key, -1, next_cycle)
            if should:
                recovered.append(q.title)
                to_mark.append((key, -1, next_cycle))
            continue

        # Threshold crossings on the way up
        for lvl in cfg.thresholds:
            if q.percentage >= lvl:
                should, _ = store.should_fire(account, key, lvl, next_cycle)
                if should:
                    fired.append((key, lvl))
                    to_mark.append((key, lvl, next_cycle))

    return fired, recovered, to_mark


_LAST_SEND_KEY = "last_alert_send_ts:"


def _last_send(account: str) -> float:
    with store.get_conn() as c:
        row = c.execute("SELECT v FROM meta WHERE k=?", (_LAST_SEND_KEY + account,)).fetchone()
        return float(row["v"]) if row else 0.0


def _mark_sent(account: str, ts: float) -> None:
    with store.get_conn() as c:
        c.execute(
            "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (_LAST_SEND_KEY + account, str(ts)),
        )


def run_once(cfg: AlertConfig | None = None) -> None:
    """Poll every account, evaluate, and send per-account alerts."""
    import config

    cfg = cfg or _load_config()
    accounts = config.load_accounts()
    if not accounts:
        log.warning("no accounts configured (accounts.toml or ZAI_API_KEY)")
        return

    for acct in accounts:
        label = acct.name or "account"
        try:
            snap = fetch_snapshot(api_key=acct.api_key)
        except ZaiError as e:
            log.error("[%s] fetch failed: %s", label, e)
            continue

        fired, recovered, to_mark = evaluate(label, snap, cfg)

        # per-account min_interval anti-spam floor
        now = time.time()
        if (fired or recovered) and now - _last_send(label) < cfg.min_interval_sec:
            log.info("[%s] suppressed by min_interval floor", label)
            continue

        if fired or recovered:
            msg = _build_message(label, snap, fired, recovered)
            recipients = acct.tg_chat_ids or cfg.tg_chat_ids
            sent_ok = _send_telegram(cfg.tg_bot_token, recipients, msg)
            if sent_ok:
                for qkey, lvl, cycle in to_mark:
                    store.mark_fired(label, qkey, lvl, cycle)
                _mark_sent(label, now)
            log.info(
                "[%s] alert sent: fired=%s recovered=%s (ok=%s)",
                label, fired, recovered, sent_ok,
            )
        else:
            log.info(
                "[%s] no alerts (5h=%s%% weekly=%s%%)",
                label,
                _pct(snap, "five_hour"),
                _pct(snap, "weekly"),
            )


def _pct(snap: Snapshot, key: str) -> str:
    q = snap.get(key)
    return str(q.percentage) if q else "?"


def main() -> int:
    import config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store.init()
    interval = config.poll_interval()

    accounts = config.load_accounts()
    log.info("zai-monitor alerts daemon starting (%d account(s), poll every %ds)",
             len(accounts), interval)
    cfg = _load_config()
    while True:
        try:
            run_once(cfg)
        except Exception:
            log.exception("unexpected error in poll loop")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

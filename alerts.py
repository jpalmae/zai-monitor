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

    import tomllib

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


def _build_message(snap: Snapshot, fired: list[tuple[str, int]], recovered: list[str]) -> str:
    level = (snap.level or "?").upper()
    try:
        import config

        acct = config.account_name()
    except Exception:
        acct = ""
    title = f"*z.ai Coding Plan ({level})* quota update"
    if acct:
        title += f" — _{acct}_"
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


def _send_telegram(cfg: AlertConfig, text: str) -> None:
    if not cfg.tg_bot_token or not cfg.tg_chat_ids:
        log.info("telegram not configured; would send:\n%s", text)
        return
    bot = telegram.Bot(token=cfg.tg_bot_token)
    for chat_id in cfg.tg_chat_ids:
        try:
            bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
            )
            log.info("sent to chat_id=%s", chat_id)
        except Exception as e:
            log.error("telegram send to %s failed: %s", chat_id, e)


def evaluate(snap: Snapshot, cfg: AlertConfig) -> tuple[list[tuple[str, int]], list[str]]:
    """Decide what to fire based on the snapshot and persistent state."""
    fired: list[tuple[str, int]] = []
    recovered: list[str] = []
    now = time.time()

    for key in ORDER:
        q = snap.get(key)
        if not q:
            continue
        next_cycle = store.cycle_id(q.next_reset.timestamp() if q.next_reset else None)
        store.record_history(key, q.percentage, q.next_reset.timestamp() if q.next_reset else None)

        # Recovered / reset notice: fire once per cycle when usage drops low.
        # The cycle_id (nextResetTime) changes on reset, which naturally
        # re-arms all threshold alerts for the new cycle.
        if q.percentage < cfg.recovered_below:
            should, _ = store.should_fire(key, -1, next_cycle)
            if should:
                recovered.append(q.title)
                store.mark_fired(key, -1, next_cycle)
            continue

        # Threshold crossings on the way up
        for lvl in cfg.thresholds:
            if q.percentage >= lvl:
                should, _ = store.should_fire(key, lvl, next_cycle)
                if should:
                    fired.append((key, lvl))
                    store.mark_fired(key, lvl, next_cycle)

    # global anti-spam floor
    if (fired or recovered) and now - _last_send() < cfg.min_interval_sec:
        log.info("suppressed by min_interval floor")
        return [], []
    return fired, recovered


_LAST_SEND_KEY = "last_alert_send_ts"


def _last_send() -> float:
    with store.get_conn() as c:
        row = c.execute("SELECT v FROM meta WHERE k=?", (_LAST_SEND_KEY,)).fetchone()
        return float(row["v"]) if row else 0.0


def _mark_sent(ts: float) -> None:
    with store.get_conn() as c:
        c.execute(
            "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (_LAST_SEND_KEY, str(ts)),
        )


def run_once(cfg: AlertConfig | None = None) -> None:
    cfg = cfg or _load_config()
    try:
        snap = fetch_snapshot()
    except ZaiError as e:
        log.error("fetch failed: %s", e)
        return

    fired, recovered = evaluate(snap, cfg)
    if fired or recovered:
        msg = _build_message(snap, fired, recovered)
        _send_telegram(cfg, msg)
        _mark_sent(time.time())
        log.info("alert sent: fired=%s recovered=%s", fired, recovered)
    else:
        log.info("no alerts (5h=%s%% weekly=%s%%)", _pct(snap, "five_hour"), _pct(snap, "weekly"))


def _pct(snap: Snapshot, key: str) -> str:
    q = snap.get(key)
    return str(q.percentage) if q else "?"


def main() -> int:
    from pathlib import Path

    import tomllib

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store.init()

    cfg_path = Path(__file__).parent / "config.toml"
    cfg_full = tomllib.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    interval = int(cfg_full.get("monitor", {}).get("poll_interval", 300))

    log.info("zai-monitor alerts daemon starting (poll every %ds)", interval)
    cfg = _load_config()
    while True:
        try:
            run_once(cfg)
        except Exception:
            log.exception("unexpected error in poll loop")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

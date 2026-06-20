"""SQLite store: usage history + alert debounce state.

History lets the TUI draw sparklines; alert_state debounces per-level-per-cycle
so each threshold fires only once until the quota resets.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "store.db"


@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS history (
                ts        REAL NOT NULL,
                quota_key TEXT NOT NULL,
                percentage INTEGER NOT NULL,
                next_reset REAL
            );
            CREATE INDEX IF NOT EXISTS idx_history_key_ts ON history(quota_key, ts);

            CREATE TABLE IF NOT EXISTS alert_state (
                quota_key TEXT NOT NULL,
                level     INTEGER NOT NULL,
                fired_at  REAL NOT NULL,
                cycle_id  TEXT NOT NULL,
                PRIMARY KEY (quota_key, level)
            );

            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY,
                v TEXT
            );
            """
        )


def record_history(
    quota_key: str, percentage: int, next_reset: float | None, ts: float | None = None
) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO history(ts, quota_key, percentage, next_reset) VALUES (?,?,?,?)",
            (ts or time.time(), quota_key, percentage, next_reset),
        )


def history(quota_key: str, limit: int = 120) -> list[tuple[float, int]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT ts, percentage FROM history WHERE quota_key=? "
            "ORDER BY ts DESC LIMIT ?",
            (quota_key, limit),
        ).fetchall()
    return [(r["ts"], r["percentage"]) for r in reversed(rows)]


def cycle_id(next_reset: float | None) -> str:
    """A cycle is identified by its reset timestamp. Resets create new cycles,
    which auto-clears stale alert_state for the previous cycle."""
    return f"{next_reset:.0f}" if next_reset else "none"


def should_fire(
    quota_key: str, level: int, next_cycle: str
) -> tuple[bool, str | None]:
    """Return (should_fire, prev_cycle). Fires only if not already fired in
    this cycle."""
    with get_conn() as c:
        row = c.execute(
            "SELECT cycle_id FROM alert_state WHERE quota_key=? AND level=?",
            (quota_key, level),
        ).fetchone()
    if row and row["cycle_id"] == next_cycle:
        return False, row["cycle_id"]
    return True, row["cycle_id"] if row else None


def mark_fired(quota_key: str, level: int, next_cycle: str) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO alert_state(quota_key, level, fired_at, cycle_id) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(quota_key, level) DO UPDATE SET "
            "fired_at=excluded.fired_at, cycle_id=excluded.cycle_id",
            (quota_key, level, time.time(), next_cycle),
        )


def clear_quota(quota_key: str) -> None:
    """Drop all alert state for a quota (used when it recovers below threshold)."""
    with get_conn() as c:
        c.execute("DELETE FROM alert_state WHERE quota_key=?", (quota_key,))


def get_meta(key: str, default: str = "") -> str:
    with get_conn() as c:
        row = c.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default


def set_meta(key: str, value: str) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO meta(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )


if __name__ == "__main__":
    init()
    print("store initialized at", DB_PATH)

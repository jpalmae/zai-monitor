"""SQLite store: usage history + alert debounce state.

Schema is versioned (meta.schema_version). v2 adds an `account` dimension so
multiple coding accounts don't collide. store.db is just a cache (history for
sparklines, debounce state), so on a schema bump we recreate the tables
(losing history is harmless — debounce simply re-arms).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "store.db"

SCHEMA_VERSION = 2


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
            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY,
                v TEXT
            );
            """
        )
        cur = c.execute("SELECT v FROM meta WHERE k='schema_version'")
        row = cur.fetchone()
        ver = int(row["v"]) if row else 1
        if ver < SCHEMA_VERSION:
            # recreate the versioned tables (cache only; safe to drop)
            c.executescript(
                """
                DROP TABLE IF EXISTS history;
                DROP TABLE IF EXISTS alert_state;
                """
            )
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS history (
                account   TEXT NOT NULL DEFAULT '',
                ts        REAL NOT NULL,
                quota_key TEXT NOT NULL,
                percentage INTEGER NOT NULL,
                next_reset REAL
            );
            CREATE INDEX IF NOT EXISTS idx_history ON history(account, quota_key, ts);

            CREATE TABLE IF NOT EXISTS alert_state (
                account   TEXT NOT NULL DEFAULT '',
                quota_key TEXT NOT NULL,
                level     INTEGER NOT NULL,
                fired_at  REAL NOT NULL,
                cycle_id  TEXT NOT NULL,
                PRIMARY KEY (account, quota_key, level)
            );
            """
        )
        c.execute(
            "INSERT INTO meta(k,v) VALUES('schema_version', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (str(SCHEMA_VERSION),),
        )


# ---- meta helpers (used for theme persistence etc.) --------------------------
def get_meta(key: str, default: str = "") -> str:
    with get_conn() as c:
        row = c.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default


def set_meta(key: str, value: str) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO meta(k,v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )


# ---- history -----------------------------------------------------------------
def record_history(
    account: str,
    quota_key: str,
    percentage: int,
    next_reset: float | None,
    ts: float | None = None,
) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO history(account, ts, quota_key, percentage, next_reset) "
            "VALUES (?,?,?,?,?)",
            (account, ts or time.time(), quota_key, percentage, next_reset),
        )


def history(account: str, quota_key: str, limit: int = 120) -> list[tuple[float, int]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT ts, percentage FROM history WHERE account=? AND quota_key=? "
            "ORDER BY ts DESC LIMIT ?",
            (account, quota_key, limit),
        ).fetchall()
    return [(r["ts"], r["percentage"]) for r in reversed(rows)]


# ---- alert debounce ----------------------------------------------------------
def cycle_id(next_reset: float | None) -> str:
    """A cycle is identified by its reset timestamp. Resets create new cycles,
    which auto-clears stale alert_state for the previous cycle."""
    return f"{next_reset:.0f}" if next_reset else "none"


def should_fire(
    account: str, quota_key: str, level: int, next_cycle: str
) -> tuple[bool, str | None]:
    """Return (should_fire, prev_cycle). Fires only if not already fired in
    this cycle for this account+quota."""
    with get_conn() as c:
        row = c.execute(
            "SELECT cycle_id FROM alert_state WHERE account=? AND quota_key=? AND level=?",
            (account, quota_key, level),
        ).fetchone()
    if row and row["cycle_id"] == next_cycle:
        return False, row["cycle_id"]
    return True, row["cycle_id"] if row else None


def mark_fired(account: str, quota_key: str, level: int, next_cycle: str) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO alert_state(account, quota_key, level, fired_at, cycle_id) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(account, quota_key, level) DO UPDATE SET "
            "fired_at=excluded.fired_at, cycle_id=excluded.cycle_id",
            (account, quota_key, level, time.time(), next_cycle),
        )


def clear_quota(account: str, quota_key: str) -> None:
    """Drop all alert state for a quota (used when it recovers below threshold)."""
    with get_conn() as c:
        c.execute(
            "DELETE FROM alert_state WHERE account=? AND quota_key=?",
            (account, quota_key),
        )


if __name__ == "__main__":
    init()
    print(f"store initialized at {DB_PATH} (schema v{SCHEMA_VERSION})")

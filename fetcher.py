"""Fetch and parse Z.ai GLM Coding Plan quotas.

Hits the internal dashboard endpoint (reverse-engineered):
    GET https://api.z.ai/api/monitor/usage/quota/limit
Auth: Bearer <coding-plan API key>.

Response shape (relevant part):
    data: {
      level: "max" | "pro" | "lite" | ...,
      limits: [
        # 5-Hour token quota (percentage only, no absolute counts)
        {type:"TOKENS_LIMIT", unit:3, number:5, percentage:N, nextResetTime:<ms>},
        # Weekly token quota (percentage only)
        {type:"TOKENS_LIMIT", unit:6, number:1, percentage:N, nextResetTime:<ms>},
        # Web Search / Reader / Zread (absolute counts)
        {type:"TIME_LIMIT", unit:5, number:1, usage:T, currentValue:U,
         remaining:R, percentage:N, nextResetTime:<ms>, usageDetails:[...]}
      ]
    }
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

# (type, unit) -> our internal key
LIMIT_MAP: dict[tuple[str, int], str] = {
    ("TOKENS_LIMIT", 3): "five_hour",
    ("TOKENS_LIMIT", 6): "weekly",
    ("TIME_LIMIT", 5): "mcp",
}

TITLES = {
    "five_hour": "5 Hours Quota",
    "weekly": "Weekly Quota",
    "mcp": "Web Search / Reader / Zread",
}

# unit_text from the frontend config
UNIT_TEXT = {
    "five_hour": "Tokens",
    "weekly": "Tokens",
    "mcp": "Times",
}


def _ts_ms(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@dataclass
class Quota:
    key: str
    title: str
    unit_text: str
    percentage: int  # 0-100
    next_reset: datetime | None
    has_counts: bool = False
    usage: int | None = None  # total (MCP only)
    used: int | None = None  # currentValue (MCP only)
    remaining: int | None = None  # (MCP only)
    usage_details: list[dict] = field(default_factory=list)


@dataclass
class Snapshot:
    fetched_at: datetime
    level: str | None
    quotas: dict[str, Quota]
    raw: dict

    def get(self, key: str) -> Quota | None:
        return self.quotas.get(key)


class ZaiError(RuntimeError):
    pass


def _token() -> str:
    key = os.getenv("ZAI_API_KEY", "").strip()
    if not key:
        raise ZaiError("ZAI_API_KEY not set (check .env)")
    return key


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=utf-8",
        "Accept-Language": "en-US,en",
        "Referer": "https://z.ai/manage-apikey/coding-plan/personal/usage",
    }


def fetch_snapshot(client: httpx.Client | None = None, api_key: str | None = None) -> Snapshot:
    """Fetch a fresh quota snapshot. Raises ZaiError on auth/logic failure.

    api_key: the coding-plan API key to use. Defaults to ZAI_API_KEY from env.
    """
    endpoint = os.getenv(
        "ZAI_QUOTA_ENDPOINT", "https://api.z.ai/api/monitor/usage/quota/limit"
    )
    token = (api_key or "").strip() or _token()
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=20.0)
    try:
        r = client.get(endpoint, headers=_headers(token), timeout=20.0)
        r.raise_for_status()
        payload = r.json()
    except httpx.RequestError as e:
        raise ZaiError(f"network error: {e}") from e
    except ValueError as e:
        raise ZaiError(f"invalid JSON: {e}") from e
    finally:
        if own_client and client is not None:
            client.close()

    code = payload.get("code")
    if code != 200 or not payload.get("success"):
        msg = payload.get("msg", "unknown")
        raise ZaiError(f"API error code={code} msg={msg!r}")

    data = payload.get("data") or {}
    limits = data.get("limits") or []
    quotas: dict[str, Quota] = {}
    for lim in limits:
        mapping = LIMIT_MAP.get((lim.get("type"), lim.get("unit")))
        if not mapping:
            continue
        q = Quota(
            key=mapping,
            title=TITLES[mapping],
            unit_text=UNIT_TEXT[mapping],
            percentage=int(lim.get("percentage") or 0),
            next_reset=_ts_ms(lim.get("nextResetTime")),
            has_counts=False,
        )
        if mapping == "mcp":
            q.has_counts = True
            q.usage = lim.get("usage")
            q.used = lim.get("currentValue")
            q.remaining = lim.get("remaining")
            q.usage_details = lim.get("usageDetails") or []
        quotas[mapping] = q

    return Snapshot(
        fetched_at=datetime.now(tz=timezone.utc),
        level=data.get("level"),
        quotas=quotas,
        raw=payload,
    )


def fetch_account(client: httpx.Client | None = None, api_key: str | None = None) -> dict:
    """Fetch account info (email, customer number). Best-effort."""
    token = (api_key or "").strip() or _token()
    url = "https://api.z.ai/api/biz/customerService/zaiUserInfo"
    own = client is None
    if own:
        client = httpx.Client(timeout=20.0)
    try:
        r = client.get(url, headers=_headers(token), timeout=20.0)
        payload = r.json()
        return payload.get("data") or {} if r.is_success else {}
    except Exception:
        return {}
    finally:
        if own and client is not None:
            client.close()


if __name__ == "__main__":
    snap = fetch_snapshot()
    print(f"level={snap.level}  fetched={snap.fetched_at.isoformat()}")
    for key in ("five_hour", "weekly", "mcp"):
        q = snap.get(key)
        if not q:
            continue
        extra = ""
        if q.has_counts:
            extra = f"  [{q.used}/{q.usage} used, {q.remaining} left]"
        reset = q.next_reset.isoformat() if q.next_reset else "?"
        print(f"  {q.title:<32} {q.percentage:>3}%{extra}  resets {reset}")

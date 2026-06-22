"""Probe the Z.ai internal quota endpoint to discover the response shape.

Run:  python3 probe.py
Reads ZAI_API_KEY / ZAI_JWT from .env (or env vars).

It tries, in order:
  1. The coding-plan API key as a Bearer token
  2. The browser JWT as a Bearer token
against a few candidate endpoints, and pretty-prints whatever comes back so we
can map the 5-hour / weekly quota fields for the real fetcher.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ZAI_API_KEY", "").strip()
JWT = os.getenv("ZAI_JWT", "").strip()

BASE = "https://api.z.ai/api"
CANDIDATES = [
    "/biz/subscription/list",
    "/biz/customer/getCustomerInfo",
    "/biz/customerService/zaiUserInfo",
]


def headers_for(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=utf-8",
        "Accept-Language": "en-US,en",
        "Referer": "https://z.ai/manage-apikey/coding-plan/personal/my-plan",
    }


def try_token(client: httpx.Client, label: str, token: str) -> bool:
    print(f"\n{'='*70}\n>>> Trying {label}\n{'='*70}")
    any_success = False
    for path in CANDIDATES:
        url = BASE + path
        try:
            r = client.get(url, headers=headers_for(token), timeout=20)
        except httpx.RequestError as e:
            print(f"  [ERR] {path}: {e}")
            continue
        body = r.text
        try:
            parsed = json.loads(body)
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pretty = body[:800]
        code = parsed.get("code") if isinstance(parsed, dict) else None
        ok = r.is_success and code in (None, 200)
        flag = "OK " if ok else "FAIL"
        print(f"\n  [{flag}] GET {path}  (http {r.status_code}, code={code})")
        print("  " + pretty.replace("\n", "\n  ")[:3000])
        if ok:
            any_success = True
    return any_success


def main() -> int:
    if not API_KEY and not JWT:
        print("ERROR: set ZAI_API_KEY and/or ZAI_JWT in .env first.", file=sys.stderr)
        return 2

    with httpx.Client(http2=False, follow_redirects=True) as client:
        got = False
        if API_KEY:
            got = try_token(client, "API KEY (coding plan)", API_KEY) or got
        if JWT and not got:
            if API_KEY:
                print("\n### API key did not work, falling back to JWT ###")
            got = try_token(client, "JWT (browser session)", JWT) or got

    print("\n" + "=" * 70)
    if got:
        print("SUCCESS: at least one endpoint returned data.")
        print("Paste the OK response(s) above so we can map the quota fields.")
    else:
        print("NO SUCCESS. Likely causes:")
        print("  - API key is NOT accepted by the /biz endpoints (expected).")
        print("  - JWT expired -> grab a fresh one from localStorage.")
        print("  - Endpoint moved -> re-run reverse engineering on the bundle.")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())

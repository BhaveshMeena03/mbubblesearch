"""Which levels the market reached, or came near, since the last look.

    .venv/bin/python scripts/check_levels.py            # print what is new, with a draft for each
    .venv/bin/python scripts/check_levels.py --json     # the same, as JSON
    .venv/bin/python scripts/check_levels.py --mark     # and remember it, so tomorrow it is old

Reads data/levels.json, fetches the price history of every listing on
it since its oldest level, and works out where each level stands today.
New since the last look is either:

    hit    the price reached the level after he said it, at or after the
           last look (the first run looks back 36 hours)
    near   the price is within 5% of a level it has not reached

Each level alerts once per kind. What has been alerted is kept in
data/.levels_seen.json, on this Mac only; --mark writes it. The daily
drafts task runs this with --mark after it has drafted the posts.

Nothing is posted. A draft is a starting point for a person to check:
a level can be reached and the moment still not be worth a post.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_levels import check_all, refresh  # noqa: E402

from app import levels  # noqa: E402

LEVELS = ROOT / "data" / "levels.json"
SEEN = ROOT / "data" / ".levels_seen.json"
FIRST_LOOK = timedelta(hours=36)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print the alerts as JSON")
    ap.add_argument("--mark", action="store_true", help="remember what was found")
    args = ap.parse_args()

    rows = json.loads(LEVELS.read_text()).get("levels", [])
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    now = datetime.now(UTC)
    since = (datetime.fromisoformat(seen["checked_at"]) if seen.get("checked_at")
             else now - FIRST_LOOK)
    async with httpx.AsyncClient(timeout=30.0) as client:
        rows = refresh(rows, await check_all(rows, client))
    found = levels.alerts(rows, seen, since)

    if args.json:
        print(json.dumps([dict(a, draft=levels.draft(a, a["alert"])) for a in found],
                         indent=1, ensure_ascii=False))
    else:
        print(f"{len(rows)} levels checked, {len(found)} new since {since:%Y-%m-%d %H:%M} UTC")
        for alert in found:
            check = alert["check"]
            print(f"\n== {alert['alert'].upper()}  {alert['symbol']} {alert['kind']} "
                  f"{alert['level']:g}  ({alert['speaker']}, {alert['date']})  "
                  f"now {check.get('price_now')}  {check.get('hit_at') or ''}")
            print(levels.draft(alert, alert["alert"]))
    if args.mark:
        SEEN.write_text(json.dumps({**levels.remember(rows, seen),
                                    "checked_at": now.isoformat(timespec="seconds")},
                                   indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

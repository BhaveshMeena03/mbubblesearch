"""Bring data/summaries.json.gz up to date with the summaries in Pinecone.

    .venv/bin/python scripts/pack_summaries.py           # fetch only what is new
    .venv/bin/python scripts/pack_summaries.py --full    # fetch every summary again

The site serves Market Bubble's summaries from this file and asks the index
only which ids it holds (app/summaries.py). This keeps the file current:
a show added by the Friday watcher or the sync workflow has its summary in
Pinecone before it has one here, and the next run fetches just that one.
Summaries written on this laptop go into the file as they are stored, so
--full is only needed if one was rewritten somewhere else.

The morning job runs it and commits the file with the other shelves.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import egress  # noqa: E402
from app.summaries import (  # noqa: E402
    SHIPPED,
    SummaryStore,
    load_shipped,
    save_shipped,
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="fetch every summary again, not just the new ones")
    args = ap.parse_args()

    before = load_shipped()
    store = SummaryStore()
    if args.full:
        store._shipped = {}
    rows = await store.list_all()
    after = {r["episode_id"]: r for r in rows if r.get("episode_id")}
    if not after:
        print("  the index returned no summaries; leaving the file alone")
        return 1
    added = sorted(set(after) - set(before))
    dropped = sorted(set(before) - set(after))
    changed = sorted(e for e in set(after) & set(before) if after[e] != before[e])
    if added or dropped or changed or not SHIPPED.exists():
        save_shipped(after)
    print(f"  {len(after)} summaries: {len(added)} new, {len(changed)} rewritten, "
          f"{len(dropped)} no longer in the index")
    for eid in added:
        print(f"    + {eid}  {after[eid].get('title', '')[:60]}")
    print(f"  pinecone read: {egress.snapshot()['since_start_mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

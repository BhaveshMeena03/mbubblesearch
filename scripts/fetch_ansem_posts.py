"""Ansem's own posts and replies, pulled from X's full archive.

The show tells you what he said out loud. It cannot tell you what he
wrote, and that is where the arguments happen: "Ansem is starting to see
it" answered with "in May btw" is a question about his timeline, and the
archive had nothing to say about it.

Two things make this cheap where the audio work was not. Every post is
his by construction, so none of the speaker machinery applies. And the
full-archive endpoint reaches January, where the user-timeline endpoint
stops after one page of six days.

    python scripts/fetch_ansem_posts.py --pages 5      # checkpoint
    python scripts/fetch_ansem_posts.py                # the rest

Runs are resumable: the newest post already saved becomes the floor, so
a second run fetches only what is missing and costs nothing for what is
already on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.x_api import XCredentials  # noqa: E402

API = "https://api.x.com/2"
OUT = ROOT / "data" / "ansem_posts.json"
HANDLE = "blknoiz06"
SINCE = "2026-01-01T00:00:00Z"

# Retweets are somebody else's words under his name, and indexing them
# would let the archive answer "what did Ansem say about X" with a quote
# he only amplified. Replies are kept: that is where he argues.
QUERY = f"from:{HANDLE} -is:retweet"

# The full-archive endpoint is rate limited to about one request a
# second. Sleeping under it is cheaper than being throttled into retries.
PAUSE = 1.1
PAGE = 100


def load() -> dict[str, dict]:
    if OUT.exists():
        return {p["id"]: p for p in json.loads(OUT.read_text())}
    return {}


def save(posts: dict[str, dict]) -> None:
    rows = sorted(posts.values(), key=lambda p: p["created_at"], reverse=True)
    OUT.write_text(json.dumps(rows, indent=1))


def flatten(row: dict) -> dict:
    """One post, with the fields worth keeping and nothing else.

    `note_tweet` carries the full text of anything over 280 characters;
    `text` is truncated with an ellipsis. Reading only `text` would cut
    off exactly the long-form posts that state a thesis.
    """
    full = (row.get("note_tweet") or {}).get("text") or row.get("text", "")
    kinds = {r.get("type") for r in (row.get("referenced_tweets") or [])}
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "text": full,
        "url": f"https://x.com/{HANDLE}/status/{row['id']}",
        "is_reply": "replied_to" in kinds,
        "is_quote": "quoted" in kinds,
        "conversation_id": row.get("conversation_id"),
        "likes": (row.get("public_metrics") or {}).get("like_count", 0),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=0,
                    help="stop after N pages (a costed checkpoint)")
    ap.add_argument("--since", default=SINCE)
    args = ap.parse_args()

    s = get_settings()
    creds = XCredentials(s.x_api_key, s.x_api_secret,
                         s.x_access_token, s.x_access_secret)
    posts = load()
    before = len(posts)
    print(f"  {before} posts already on disk")

    url = f"{API}/tweets/search/all"
    token, pages, read = None, 0, 0
    async with httpx.AsyncClient(timeout=40) as http:
        while True:
            params = {
                "query": QUERY,
                "max_results": str(PAGE),
                "start_time": args.since,
                "tweet.fields": ("created_at,public_metrics,referenced_tweets,"
                                 "note_tweet,conversation_id"),
            }
            if token:
                params["pagination_token"] = token
            headers = {"Authorization": creds.header("GET", url, params)}
            r = await http.get(url, params=params, headers=headers)
            if r.status_code != 200:
                # Print the body: a 429 and a bad query fail the same way
                # from outside, and guessing which one costs another run.
                print(f"  HTTP {r.status_code}: {r.text[:200]}")
                break
            body = r.json()
            rows = body.get("data", [])
            if not rows:
                print("  no more posts")
                break
            for row in rows:
                posts[row["id"]] = flatten(row)
            read += len(rows)
            pages += 1
            oldest = min(x["created_at"] for x in rows)[:10]
            print(f"  page {pages:3d}  +{len(rows):3d} posts  "
                  f"back to {oldest}  ({len(posts)} held)", flush=True)
            save(posts)          # every page, so a crash costs one page
            token = body.get("meta", {}).get("next_token")
            if not token:
                print("  reached the end of the archive")
                break
            if args.pages and pages >= args.pages:
                print(f"  stopping at the {args.pages}-page checkpoint")
                break
            await asyncio.sleep(PAUSE)

    save(posts)
    added = len(posts) - before
    dates = [p["created_at"][:10] for p in posts.values()]
    print(f"\n  {read} posts read this run, {added} new, {len(posts)} total")
    if dates:
        print(f"  span {min(dates)} .. {max(dates)}")
    print(f"  about ${read * 0.001:,.2f} in X reads this run")
    print(f"  -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

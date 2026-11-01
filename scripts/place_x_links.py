"""Our posts that link a show's X broadcast at a second, for the extension.

    .venv/bin/python scripts/place_x_links.py --search
    .venv/bin/python scripts/place_x_links.py --search --since 2026-09-01

Most of what @mbubbleSearch posts about Market Bubble, MCG and ThreadGuy is
a quote and a link to the broadcast at the second it was said, not an
uploaded clip (a clip of a show that aired on X would only send people
off the broadcast). On X those posts show the broadcast as a card, and
the second the link points to is not on the page: X keeps the link's
?t= in the post's data and drops it from what it draws. So it is read
here, from X's search, which returns each post's links in full.

--search asks for posts from @mbubbleSearch since the newest one already
read (kept in data/x_links_search.json), through
the recent search; --since reaches further back through the full-archive
search, for a first run. X charges for each post returned, about half a
cent, and we post about eight of these a day.

Writes data/x_links.json, {post: {broadcast, t}}: the broadcast and the
second on X's clock. Which show that is, and which second of its
transcript, the server works out from the pairs files it already reads
(app/x_posts.py), so a show paired later is picked up without asking X
again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "x_links.json"
SEARCHED = ROOT / "data" / "x_links_search.json"
ACCOUNT = "mbubbleSearch"
# Every original post of ours, not url:"i/broadcasts": a post over 280
# characters keeps its links in note_tweet, which the url: operator does
# not search, and most of ours are that long. Measured 2 Nov 2026: url:
# found 31 since 1 Oct and missed the Mando post linking ThreadGuy's
# stream at 1:40:07. The links are picked out here instead.
QUERY = f"from:{ACCOUNT} -is:retweet -is:reply"
_BROADCAST = re.compile(r"^https://(?:x|twitter)\.com/i/broadcasts/([A-Za-z0-9]{8,20})/?$")


def link_of(post: dict) -> dict | None:
    """The first broadcast link in the post with a second on it, as
    {broadcast, t}, or None. A long post's links are in note_tweet."""
    urls = [*((post.get("entities") or {}).get("urls") or []),
            *(((post.get("note_tweet") or {}).get("entities") or {}).get("urls") or [])]
    for url in urls:
        parts = urlsplit(str(url.get("expanded_url") or ""))
        found = _BROADCAST.match(f"{parts.scheme}://{parts.netloc}{parts.path}")
        t = (parse_qs(parts.query).get("t") or [""])[0]
        if found and t.isdigit() and int(t) < 24 * 3600:
            return {"broadcast": f"https://x.com/i/broadcasts/{found.group(1)}", "t": int(t)}
    return None


async def search(since_id: str | None, since_day: str | None) -> list[dict]:
    """Our posts with a broadcast link, as X returns them."""
    import httpx

    from app.config import get_settings
    from app.x_api import API, XCredentials

    settings = get_settings()
    cred = XCredentials(settings.x_api_key, settings.x_api_secret,
                        settings.x_access_token, settings.x_access_secret)
    url = f"{API}/tweets/search/{'all' if since_day else 'recent'}"
    found: list[dict] = []
    token = None
    async with httpx.AsyncClient(timeout=30) as http:
        for _ in range(10):                      # 1,000 posts at most a run
            params = {"query": QUERY, "max_results": "100",
                      "tweet.fields": "entities,note_tweet"}
            if since_day:
                params["start_time"] = f"{since_day}T00:00:00Z"
            elif since_id:
                params["since_id"] = since_id
            if token:
                params["next_token"] = token
            response = await http.get(url, params=params,
                                      headers={"Authorization": cred.header("GET", url, params)})
            if response.status_code != 200:
                raise RuntimeError(f"X search answered HTTP {response.status_code}")
            body = response.json()
            found += body.get("data") or []
            token = (body.get("meta") or {}).get("next_token")
            if not token:
                break
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--search", action="store_true", help="ask X for our new link posts")
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="with --search: from this day, through the full-archive search")
    args = ap.parse_args()
    if not args.search:
        ap.error("nothing to do without --search")
    if args.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
        ap.error("--since takes a day, YYYY-MM-DD")

    from app.config import redact

    links = json.loads(OUT.read_text()) if OUT.exists() else {}
    state = json.loads(SEARCHED.read_text()) if SEARCHED.exists() else {}
    try:
        posts = asyncio.run(search(state.get("newest"), args.since))
    except Exception as exc:                                    # noqa: BLE001
        print(f"  could not ask X: {type(exc).__name__}: {redact(str(exc))[:200]}")
        return 1
    added = 0
    for post in posts:
        link = link_of(post)
        if link and re.fullmatch(r"[0-9]{5,25}", post.get("id", "")):
            added += post["id"] not in links
            links[post["id"]] = link
    OUT.write_text(json.dumps(dict(sorted(links.items(), key=lambda kv: int(kv[0]))), indent=1) + "\n")
    newest = max([p["id"] for p in posts] + ([state["newest"]] if state.get("newest") else []),
                 key=int, default=None)
    if newest:
        SEARCHED.write_text(json.dumps({"newest": newest}) + "\n")
    print(f"  {len(posts)} posts read, {added} new link posts; {len(links)} in {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

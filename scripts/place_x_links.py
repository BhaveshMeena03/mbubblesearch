"""Our posts that link a show at a second, on X or YouTube, for the extension.

    .venv/bin/python scripts/place_x_links.py --search
    .venv/bin/python scripts/place_x_links.py --search --since 2026-09-01

Most of what @mbubbleSearch posts about Market Bubble, MCG and ThreadGuy is
a quote and a link to the broadcast at the second it was said, not an
uploaded clip (a clip of a show that aired on X would only send people
off the broadcast). Before October most linked the show's YouTube copy
instead. On X those posts show the link as a card, and the second it
points to is not on the page: X keeps the link's ?t= in the post's data
and drops it from what it draws. So it is read here, from X's API, which
returns each post's links in full.

Every post the account wrote counts, replies too: the receipts under
other people's posts are the ones most people see, and the bot's answers
link the moment they quote. Only reposts are left out.

--search reads @mbubbleSearch's own timeline from the newest post already
read (kept in data/x_links_search.json); --since reads it from a day, for
a first run. Our own timeline is an owned read, $0.001 a post, a fifth of
what X's search charges, and it has no seven-day edge. Search was also
not the whole answer: measured 2 Nov 2026, its -is:reply let through 39
of our October replies and dropped 27 others. The timeline reaches the
3,200 most recent posts; the account had 739 when --since 2026-08-22
read it back to the first.

Writes data/x_links.json, {post: {broadcast, t}} or {post: {youtube, t}}:
the broadcast and the second on X's clock, or the video and the second on
YouTube's. Which show that is, and which second of its
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
EXCLUDE = "retweets"
_BROADCAST = re.compile(r"^https://(?:x|twitter)\.com/i/broadcasts/([A-Za-z0-9]{8,20})/?$")
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
# YouTube writes a start as 1129, 1129s or 18m49s.
_START = re.compile(r"(?:(\d{1,2})h)?(?:(\d{1,4})m)?(?:(\d{1,5})s?)?")


def link_of(post: dict) -> dict | None:
    """The show the post links at a second, as {broadcast, t} or
    {youtube, t}, or None. A broadcast wins over a YouTube link in the same
    post: it opens on X. A long post's links are in note_tweet."""
    urls = [*((post.get("entities") or {}).get("urls") or []),
            *(((post.get("note_tweet") or {}).get("entities") or {}).get("urls") or [])]
    found = [shown for url in urls if (shown := _shown(str(url.get("expanded_url") or "")))]
    return next((f for f in found if "broadcast" in f), found[0] if found else None)


def _shown(url: str) -> dict | None:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    broadcast = _BROADCAST.match(f"{parts.scheme}://{parts.netloc}{parts.path}")
    if broadcast:
        t = (query.get("t") or [""])[0]
        if t.isdigit() and int(t) < 24 * 3600:
            return {"broadcast": f"https://x.com/i/broadcasts/{broadcast.group(1)}", "t": int(t)}
        return None
    if parts.scheme != "https":
        return None
    if parts.netloc in ("www.youtube.com", "youtube.com", "m.youtube.com") and parts.path == "/watch":
        video = (query.get("v") or [""])[0]
    elif parts.netloc == "youtu.be":
        video = parts.path.strip("/")
    else:
        return None
    start = _START.fullmatch((query.get("t") or [""])[0])
    if not (_VIDEO.fullmatch(video) and start and any(start.groups())):
        return None
    h, m, sec = (int(g or 0) for g in start.groups())
    t = h * 3600 + m * 60 + sec
    return {"youtube": video, "t": t} if t < 24 * 3600 else None


async def timeline(since_id: str | None, since_day: str | None) -> list[dict]:
    """Our own original posts, as X returns them, newest first. A long
    post's links are in note_tweet, so that is asked for too."""
    import httpx

    from app.config import get_settings
    from app.x_api import API, XCredentials

    settings = get_settings()
    account = str(settings.x_bot_user_id or "")
    if not re.fullmatch(r"[0-9]{1,25}", account):
        raise RuntimeError("X_BOT_USER_ID is not set to the account's numeric id")
    cred = XCredentials(settings.x_api_key, settings.x_api_secret,
                        settings.x_access_token, settings.x_access_secret)
    url = f"{API}/users/{account}/tweets"
    found: list[dict] = []
    token = None
    async with httpx.AsyncClient(timeout=30) as http:
        for _ in range(10):                      # 1,000 posts at most a run
            params = {"exclude": EXCLUDE, "max_results": "100",
                      "tweet.fields": "entities,note_tweet"}
            if since_day:
                params["start_time"] = f"{since_day}T00:00:00Z"
            elif since_id:
                params["since_id"] = since_id
            if token:
                params["pagination_token"] = token
            response = await http.get(url, params=params,
                                      headers={"Authorization": cred.header("GET", url, params)})
            if response.status_code != 200:
                raise RuntimeError(f"X answered HTTP {response.status_code}")
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
                    help="with --search: from this day, for a first run")
    args = ap.parse_args()
    if not args.search:
        ap.error("nothing to do without --search")
    if args.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
        ap.error("--since takes a day, YYYY-MM-DD")

    from app.config import redact

    links = json.loads(OUT.read_text()) if OUT.exists() else {}
    state = json.loads(SEARCHED.read_text()) if SEARCHED.exists() else {}
    try:
        posts = asyncio.run(timeline(state.get("newest"), args.since))
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

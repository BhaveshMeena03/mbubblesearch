"""Pair ThreadGuy's X broadcasts with the YouTube copies of the same shows.

    .venv/bin/python scripts/pair_threadguy_broadcasts.py --search
    .venv/bin/python scripts/pair_threadguy_broadcasts.py --dry-run
    .venv/bin/python scripts/pair_threadguy_broadcasts.py --only 1pJdRDqeOYnKW

He goes live from @notthreadguy on X and YouTube at once, the market open
every weekday and the interviews, and the archive is the YouTube copy.
This finds which X broadcast is which show, so the browser extension can
put its button on his posts and a moment can open on X at the same second.

--search asks X for the broadcasts: every post from @notthreadguy that
links the broadcast player, from 1 Oct 2026 or after the newest one
already on file, through the full-archive search, which reaches past the
recent search's seven days. One post a show, so a month is about 25 posts
read. They are kept in data/threadguy_x_broadcasts.json, so pairing again
reads nothing from X.

Title and date only nominate a candidate. The proof is the audio, as in
scripts/pair_mcg_broadcasts.py and pair_rasmr_broadcasts.py: ninety
seconds of the X broadcast every twenty minutes, transcribed and matched by
eight-word runs, and where two slices disagree the stretch between them is
heard again until the change is pinned down. What it is matched against is
the show's own YouTube caption track rather than the index, which is the
same clock and reads nothing back out of Pinecone. His streams have no
caption track (checked 1 Nov 2026: "has no automatic captions"), so for
those the transcript comes back out of the index, one episode's passages,
as the MCG and rasmr pairing scripts read theirs: about 200 KB a show.

Writes data/threadguy_broadcast_links.json, {youtube id: {broadcast,
offset or sections, post}}, where X second = YouTube second + offset.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.captions import coalesce, parse_vtt  # noqa: E402
from app.config import get_settings, redact  # noqa: E402
from app.x_api import API, XCredentials  # noqa: E402
from scripts.find_new_broadcasts import broadcast_link  # noqa: E402
from scripts.pair_mcg_broadcasts import (  # noqa: E402
    chart,
    described,
    hear,
    listening_points,
    offset,
    shape,
    stretches,
)

BROADCASTS = ROOT / "data" / "threadguy_x_broadcasts.json"
SHELF = ROOT / "data" / "threadguy_index.json"
OUT = ROOT / "data" / "threadguy_broadcast_links.json"

SHOW = "notthreadguy"
QUERY = f'from:{SHOW} url:"i/broadcasts" -is:retweet -is:reply'
# The first day paired, the same line MCG's pairs start from.
FROM = "2026-10-01T00:00:00Z"
_LINK = re.compile(r"https?://\S+")


def title_of(text: str) -> str:
    """The post's words without the link X appends, as he titled it."""
    return _LINK.sub("", html.unescape(text or "")).strip()


def as_broadcast(post: dict) -> dict | None:
    url = broadcast_link(post)
    if not url:
        return None
    return {"broadcast": url.split("?", 1)[0], "post": post["id"],
            "posted": post.get("created_at", ""), "title": title_of(post.get("text", ""))}


async def search(since_id: str | None) -> list[dict]:
    """Every broadcast post from 1 Oct, or after since_id, oldest first."""
    settings = get_settings()
    cred = XCredentials(settings.x_api_key, settings.x_api_secret,
                        settings.x_access_token, settings.x_access_secret)
    url = f"{API}/tweets/search/all"
    found: list[dict] = []
    token = None
    async with httpx.AsyncClient(timeout=30) as http:
        for _ in range(10):                     # 1,000 posts at most, ever
            params = {"query": QUERY, "max_results": "100",
                      "tweet.fields": "created_at,entities"}
            if since_id:
                params["since_id"] = since_id
            else:
                params["start_time"] = FROM
            if token:
                params["next_token"] = token
            response = await http.get(url, params=params,
                                      headers={"Authorization": cred.header("GET", url, params)})
            if response.status_code != 200:
                raise RuntimeError(f"X search answered HTTP {response.status_code}")
            body = response.json()
            found += [b for b in map(as_broadcast, body.get("data") or []) if b]
            token = (body.get("meta") or {}).get("next_token")
            if not token:
                break
    return sorted(found, key=lambda b: int(b["post"]))


def merge(known: list[dict], new: list[dict]) -> list[dict]:
    by_post = {b["post"]: b for b in known}
    by_post.update({b["post"]: b for b in new})
    return sorted(by_post.values(), key=lambda b: int(b["post"]))


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(title or "").lower()).strip()


def candidates(cast: dict, shows: list[dict], paired: dict) -> list[dict]:
    """The YouTube copies worth listening for: the one with the same title,
    else those published within a day of the post, nearest first."""
    open_rows = [r for r in shows if r["id"] not in paired]
    same = [r for r in open_rows if cast["title"] and norm(r.get("title", "")) == norm(cast["title"])]
    if same:
        return same
    day = date.fromisoformat(cast["posted"][:10])

    def apart(row: dict) -> int:
        try:
            return abs((date.fromisoformat(row.get("published_at", "")[:10]) - day).days)
        except ValueError:
            return 99
    return sorted((r for r in open_rows if apart(r) <= 1), key=apart)


def captions(video_id: str, work: Path) -> list[dict]:
    """The show's YouTube caption track, the English-audio one first."""
    subprocess.run(
        [str(ROOT / ".venv" / "bin" / "yt-dlp"), "--socket-timeout", "30", "--no-warnings", "-q",
         "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", "en-orig,en",
         "--sub-format", "vtt", "-o", str(work / "%(id)s.%(ext)s"),
         f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, timeout=240)
    tracks = sorted(work.glob(f"{video_id}*.vtt"), key=lambda p: (".en-orig." not in p.name, p.name))
    return coalesce(parse_vtt(tracks[0].read_text())) if tracks else []


_INDEX = None


def indexed(video_id: str) -> list[dict]:
    """The show's lines out of its own passages in the index, for a show
    YouTube has no captions for."""
    global _INDEX
    from pinecone import Pinecone

    from app.mcg_transcript import rebuild

    settings = get_settings()
    if _INDEX is None:
        _INDEX = Pinecone(api_key=settings.pinecone_api_key).Index(settings.pinecone_index)
    return rebuild(_INDEX, "threadguy", settings.embedding_dimension, video_id)


def measure(url: str, row: dict) -> dict | None:
    """How this broadcast's clock sits against the YouTube copy, or None
    when it is not the same show."""
    with tempfile.TemporaryDirectory() as tmp:
        lines = captions(row["id"], Path(tmp)) or indexed(row["id"])
        if not lines:
            return None
        found = chart(lambda at: offset(hear(url, at, Path(tmp)), at, lines),
                      listening_points(row.get("seconds") or lines[-1]["t"]))
    mapped = stretches(found)
    return shape(mapped) if mapped else None


def write(pairs: dict) -> None:
    OUT.write_text(json.dumps(pairs, indent=1, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", action="store_true", help="ask X for new broadcasts first")
    ap.add_argument("--dry-run", action="store_true", help="measure, write nothing")
    ap.add_argument("--only", nargs="+", default=[], metavar="BROADCAST",
                    help="pair only these broadcast ids")
    args = ap.parse_args()

    casts = json.loads(BROADCASTS.read_text())["broadcasts"] if BROADCASTS.exists() else []
    if args.search:
        newest = max((c["post"] for c in casts), key=int, default=None)
        try:
            new = asyncio.run(search(newest))
        except Exception as exc:                                # noqa: BLE001
            print(f"  could not ask X: {redact(str(exc))[:200]}")
            return 1
        casts = merge(casts, new)
        if not args.dry_run:
            BROADCASTS.write_text(json.dumps({"broadcasts": casts}, indent=1) + "\n")
        print(f"  {len(new)} broadcast posts from X; {len(casts)} on file")

    shows = json.loads(SHELF.read_text())
    pairs = json.loads(OUT.read_text()) if OUT.exists() else {}
    taken = {p["broadcast"] for p in pairs.values()}
    named = set(args.only)
    added = 0
    for cast in casts:
        url = cast["broadcast"]
        if url in taken or (named and url.rsplit("/", 1)[-1] not in named):
            continue
        label = f"{cast['posted'][:10]} {url.rsplit('/', 1)[-1]}"
        for row in candidates(cast, shows, pairs):
            try:
                clocks = measure(url, row)
            except Exception as exc:                            # noqa: BLE001
                print(f"  {label}: could not listen ({redact(str(exc))[:100]})")
                break
            if clocks:
                pairs[row["id"]] = {"broadcast": url, **clocks, "post": cast["post"]}
                added += 1
                print(f"  {label} = {row['id']}  {described(clocks)}  {row['title'][:50]}",
                      flush=True)
                # Kept as each is found, so a run stopped by a stuck
                # broadcast keeps what it measured.
                if not args.dry_run:
                    write(pairs)
                break
        else:
            print(f"  {label}: no YouTube copy on the shelf matches by ear  {cast['title'][:50]}",
                  flush=True)
    print(f"\n  {added} new; {len(pairs)} paired in all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

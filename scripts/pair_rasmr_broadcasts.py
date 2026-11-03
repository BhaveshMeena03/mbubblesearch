"""Pair rasmr's X broadcasts with the YouTube copies of the same streams.

    .venv/bin/python scripts/pair_rasmr_broadcasts.py --search
    .venv/bin/python scripts/pair_rasmr_broadcasts.py
    .venv/bin/python scripts/pair_rasmr_broadcasts.py --dry-run
    .venv/bin/python scripts/pair_rasmr_broadcasts.py --only 1YGNrblaopvGw

He goes live on X and YouTube at once, and the archive is the YouTube
copy. This finds which X broadcast is which stream, so a moment can also
open on X at the same second, and the browser extension puts its button
on his posts. The broadcasts are kept in data/rasmr_x_broadcasts.json,
read off his timeline by hand until 28 Oct 2026. --search, which the
morning job runs, adds the ones posted since, with the same X search
pair_threadguy_broadcasts.py makes for ThreadGuy (one post a stream), and
then listens only for the unpaired ones of the last week: a stream whose
YouTube copy came late is caught the next morning, and the X-only shows,
which never match, are not heard again every day.

Title and date only nominate a candidate. The proof is the audio, as in
scripts/pair_mcg_broadcasts.py: ninety seconds of the X broadcast every
twenty minutes, transcribed and matched against the indexed transcript by
eight-word runs, and where two slices disagree the stretch between them
is heard again until the change is pinned down. Two slices must be the
show, or the broadcast is not paired. Costs about $0.001 of Groq a slice.

--only names the broadcasts to pair, by id, so new ones can be paired
without listening again to the old ones that never matched (an X-only
show is near a YouTube stream by date and gets heard against it).

Writes data/rasmr_broadcast_links.json, {youtube id: {broadcast, offset,
post}}, where X second = YouTube second + offset, or "sections" in place
of offset for a show whose clocks moved partway (app/mcg_on_x.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pinecone import Pinecone  # noqa: E402

from app.config import get_settings, redact  # noqa: E402
from app.mcg_transcript import rebuild  # noqa: E402
from scripts.pair_mcg_broadcasts import (  # noqa: E402
    chart,
    described,
    hear,
    listening_points,
    offset,
    shape,
    stretches,
)

BROADCASTS = ROOT / "data" / "rasmr_x_broadcasts.json"
QUERY = 'from:rasmr_eth url:"i/broadcasts" -is:retweet -is:reply'
RECENT_DAYS = 7
SHELF = ROOT / "data" / "rasmr_index.json"
OUT = ROOT / "data" / "rasmr_broadcast_links.json"


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:45]


def candidates(cast: dict, streams: list[dict], paired: dict) -> list[dict]:
    """The YouTube streams worth listening for: one with the same title,
    else the ones published within a day of the post, nearest first."""
    same = [r for r in streams if cast["title"] and norm(r["title"]) == norm(cast["title"])]
    if same:
        return same
    day = date.fromisoformat(cast["posted"][:10])
    near = [r for r in streams if r["id"] not in paired
            and abs((date.fromisoformat(r["published_at"]) - day).days) <= 1]
    return sorted(near, key=lambda r: abs((date.fromisoformat(r["published_at"]) - day).days))


def wanted(casts: list[dict], taken: set[str], only: list[str],
           since: str = "") -> list[dict]:
    """The broadcasts to listen for, oldest first: every unpaired one, only
    those named, or only those posted from `since` (an ISO date)."""
    named = set(only)
    return sorted((c for c in casts if c["broadcast"] not in taken
                   and (not named or c["broadcast"].rsplit("/", 1)[-1] in named)
                   and c["posted"][:10] >= since),
                  key=lambda c: c["posted"])


def measure(index, dimension: int, url: str, row: dict) -> dict | None:
    """How this broadcast's clock sits against the indexed stream, or
    None when it is not the same show."""
    lines = rebuild(index, "rasmr", dimension, row["id"])
    if not lines:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        found = chart(lambda at: offset(hear(url, at, Path(tmp)), at, lines),
                      listening_points(row["seconds"]))
    mapped = stretches(found)
    return shape(mapped) if mapped else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", action="store_true",
                    help="ask X for new broadcasts, then pair the last week's")
    ap.add_argument("--dry-run", action="store_true", help="measure, write nothing")
    ap.add_argument("--only", nargs="+", default=[], metavar="BROADCAST",
                    help="pair only these broadcast ids")
    args = ap.parse_args()

    kept = json.loads(BROADCASTS.read_text())
    casts = kept["broadcasts"]
    since = ""
    if args.search:
        from scripts.pair_threadguy_broadcasts import merge, search

        newest = max((c["post"] for c in casts), key=int, default=None)
        try:
            new = asyncio.run(search(newest, QUERY))
        except Exception as exc:                                # noqa: BLE001
            print(f"  could not ask X: {type(exc).__name__}: {redact(str(exc))[:200]}")
            return 1
        casts = merge(casts, new)
        if not args.dry_run:
            BROADCASTS.write_text(json.dumps({**kept, "broadcasts": casts}, indent=1) + "\n")
        print(f"  {len(new)} broadcast posts from X; {len(casts)} on file")
        since = (datetime.now(UTC).date() - timedelta(days=RECENT_DAYS)).isoformat()
    streams = [r for r in json.loads(SHELF.read_text())
               if r.get("format") == "stream" and r.get("source") != "x"]
    pairs = json.loads(OUT.read_text()) if OUT.exists() else {}
    taken = {p["broadcast"] for p in pairs.values()}
    settings = get_settings()
    index = Pinecone(api_key=settings.pinecone_api_key).Index(settings.pinecone_index)

    added = 0
    for cast in wanted(casts, taken, args.only, since):
        url = cast["broadcast"]
        if url in taken:
            continue                     # a second post linking one paired just now
        label = f"{cast['posted'][:10]} {url.rsplit('/', 1)[-1]}"
        for row in candidates(cast, streams, pairs):
            try:
                clocks = measure(index, settings.embedding_dimension, url, row)
            except Exception as exc:                            # noqa: BLE001
                print(f"  {label}: could not listen ({str(exc)[:100]})")
                break
            if clocks:
                pairs[row["id"]] = {"broadcast": url, **clocks, "post": cast["post"]}
                taken.add(url)
                added += 1
                print(f"  {label} = {row['id']}  {described(clocks)}  {row['title'][:50]}")
                # Kept as it is found. Written only at the end, a run
                # stopped after a stuck broadcast lost the pair it had
                # already measured.
                if not args.dry_run:
                    OUT.write_text(json.dumps(pairs, indent=1, sort_keys=True) + "\n")
                break
        else:
            print(f"  {label}: no indexed YouTube copy matches by ear")

    if added and not args.dry_run:
        OUT.write_text(json.dumps(pairs, indent=1, sort_keys=True) + "\n")
    print(f"\n  {added} new; {len(pairs)} paired in all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Long-form finance interviews -- Fink, Dalio, Schwarzman, the panels.

A fourth archive, kept apart from the other three the way they are kept
apart from each other: its own namespace, its own shelf, and routing
terms that were counted against the Market Bubble transcripts before
they were allowed to exist. Nothing here writes to the broadcast, the
Musk or the MCG corpus, and the namespace is asserted before a single
vector is embedded.

Takes YouTube URLs rather than a channel, because there is no single
channel: Fink appears on Bloomberg, Milken, the FII panels and Davos,
and the good material is whoever posted the full session.

    python scripts/ingest_tradfi.py --list
    python scripts/ingest_tradfi.py https://youtu.be/XXXX --subject "Larry Fink"

Transcription is local, the same path MCG uses, so a run costs time and
no money.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import episode_store  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from app.provenance import drop_hallucinated  # noqa: E402
from app.schemas import Episode  # noqa: E402
from scripts.ingest_mcg import (  # noqa: E402
    YTDLP,
    fetch_audio,
    proxy_args,
    published,
    transcribe,
)

SHELF = ROOT / "data" / "tradfi_episodes.json"

# The corpora this must never write into. Asserted at runtime rather than
# trusted, because a namespace is a string and a typo in one is silent:
# it would embed a BlackRock panel into the archive the account's whole
# standing rests on, and nothing would fail.
FORBIDDEN = {"podcast", "elon", "mcg", "summaries", "assets", "ansem_posts"}

_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})")


def video_id(url: str) -> str:
    found = _ID.search(url)
    if not found:
        raise SystemExit(f"could not find a video id in {url!r}")
    return found.group(1)


def title_of(video_id: str) -> str:
    """The recording's real title.

    Worth a call of its own: the title is what a citation shows and what
    an answer names the source by, and "Larry Fink: OL2dubQ0_CQ" tells a
    reader nothing about which conversation they are being pointed at.
    """
    done = subprocess.run(
        [YTDLP, "--no-warnings", *proxy_args(), "--print", "%(title)s",
         f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, timeout=300)
    return (done.stdout or "").strip()


def shelf() -> list[dict]:
    return episode_store.load(SHELF)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="YouTube URLs to add")
    ap.add_argument("--subject", default="",
                    help="who is speaking, e.g. 'Larry Fink' — recorded on "
                         "the shelf so the archive can grow past one person")
    ap.add_argument("--list", action="store_true",
                    help="show what is already indexed and stop")
    args = ap.parse_args()

    rows = shelf()
    if args.list or not args.urls:
        hours = sum(r.get("seconds", 0) for r in rows) / 3600
        print(f"  {len(rows)} recordings, {hours:.1f} hours")
        for r in rows:
            print(f"    {r['episode_id']}  {r.get('seconds', 0) // 60:>4}m  "
                  f"{r.get('subject', '?'):<16} {r['title'][:54]}")
        return 0

    settings = get_settings()
    namespace = settings.tradfi_namespace
    # Belt and braces. `tradfi_namespace` is a setting, and a setting can
    # be overridden by an env var on the machine that runs this.
    if namespace in FORBIDDEN:
        raise SystemExit(
            f"refusing to run: namespace {namespace!r} is another archive. "
            f"This script only ever writes to its own.")
    index = PodcastIndex(namespace=namespace)
    print(f"  writing to namespace {namespace!r} "
          f"in index {settings.pinecone_index!r}")

    known = {r["episode_id"] for r in rows}
    added = 0
    for url in args.urls:
        vid = video_id(url)
        if vid in known:
            print(f"  {vid} already indexed — skipping")
            continue
        print(f"\n  {vid}  fetching…", flush=True)
        audio = fetch_audio(vid)
        print(f"     {audio.stat().st_size // 1_000_000} MB, transcribing…",
              flush=True)
        segments = transcribe(audio)
        kept, dropped = drop_hallucinated(segments)
        if dropped:
            print(f"     dropped hallucinated: {', '.join(dropped)}")
        if not kept:
            print("     nothing transcribed — skipping rather than "
                  "shelving an empty recording")
            continue

        title = title_of(vid) or vid
        if args.subject and args.subject.lower() not in title.lower():
            title = f"{args.subject}: {title}"
        episode = Episode(
            episode_id=vid, title=title,
            url=f"https://www.youtube.com/watch?v={vid}",
            platform="youtube", published_at=published(vid) or None,
            segments=kept,
        )
        windows = await index.ingest([episode])
        print(f"     {len(kept)} segments -> {windows} searchable passages")

        # Written last, and only after the embedding returns: the shelf is
        # what the server reads to decide the archive exists, so a row for
        # a recording whose vectors never landed would make an empty
        # archive answer confidently.
        row = {
            "episode_id": vid,
            "title": title,
            "subject": args.subject or "unknown",
            "url": episode.url,
            "published_at": episode.published_at,
            "seconds": int(max(s["t"] for s in kept)),
            "segments": kept,
        }
        # Through the store, which holds an exclusive lock and replaces
        # the file atomically. Writing it directly would race a second
        # ingest and lose whichever finished first -- the hazard that
        # store exists for, and the reason a test forbids write_text on
        # a shelf anywhere in scripts/.
        episode_store.merge([row], path=SHELF)
        rows = shelf()
        added += 1

    hours = sum(r.get("seconds", 0) for r in rows) / 3600
    print(f"\n  {added} added · shelf now {len(rows)} recordings, "
          f"{hours:.1f} hours")
    if added:
        print("  repack for the image:  python scripts/pack_episodes.py "
              "data/tradfi_episodes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

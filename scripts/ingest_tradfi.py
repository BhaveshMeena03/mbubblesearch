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
    python scripts/ingest_tradfi.py --redo DLFXUkOc_7I --subject "Kevin Warsh"

--redo puts a recording in again from the transcript already on the shelf,
under the name given. It exists because one was filed under the wrong
person: the 29 July 2026 FOMC press conference went in as Jerome Powell's,
on the assumption that the Fed chair was still Jerome Powell. It opens "My
second FOMC committee meeting as chairman" and the room says "Chairman
Warsh". Forty-five minutes of one man's words were cited as another's for
ten weeks. The subject of a recording is read off the recording, not off
who usually gives it.

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
from app.provenance import drop_hallucinated, strip_speaker_labels  # noqa: E402
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


def retitled(title: str, was: str, now: str) -> str:
    """The title under a corrected subject: the old name's prefix comes
    off and the new one goes on, unless the title already carries it."""
    if was and title.lower().startswith(f"{was.lower()}: "):
        title = title[len(was) + 2:]
    if now and now.lower() not in title.lower():
        title = f"{now}: {title}"
    return title


def forget(index, namespace: str, vid: str, dimension: int) -> int:
    """Take a recording's passages out, so none of the old ones are left
    beside the new: a cleaned transcript windows differently."""
    probe = [0.0] * dimension
    probe[0] = 1.0
    # A set, because a delete takes a moment to show: the next query can
    # hand back ids that are already gone, and counting them again said
    # 48 passages had come out of a recording that only ever had 24.
    removed: set[str] = set()
    for _ in range(5):
        found = index.query(vector=probe, top_k=1000, namespace=namespace,
                            include_metadata=False,
                            filter={"episode_id": {"$eq": vid}})
        ids = [m["id"] for m in found.get("matches", [])]
        if not ids:
            break
        for at in range(0, len(ids), 100):
            index.delete(ids=ids[at:at + 100], namespace=namespace)
        removed.update(ids)
    return len(removed)


async def redo(vid: str, subject: str) -> int:
    rows = shelf()
    row = next((r for r in rows if r["episode_id"] == vid), None)
    if row is None:
        print(f"  {vid} is not on the shelf")
        return 1
    settings = get_settings()
    namespace = settings.tradfi_namespace
    if namespace in FORBIDDEN:
        raise SystemExit(f"refusing to run: namespace {namespace!r} is "
                         f"another archive.")
    kept, dropped = drop_hallucinated(row["segments"])
    kept, labels = strip_speaker_labels(kept)
    for note in dropped + labels:
        print(f"     removed: {note}")
    was = row.get("subject", "")
    subject = subject or was
    title = retitled(row["title"], was, subject)
    if title != row["title"]:
        print(f"     {row['title']!r}\n  -> {title!r}")
    index = PodcastIndex(namespace=namespace)
    gone = forget(index.index, namespace, vid, settings.embedding_dimension)
    episode = Episode(episode_id=vid, title=title, url=row["url"],
                      platform="youtube",
                      published_at=row.get("published_at"), segments=kept)
    windows = await index.ingest([episode])
    print(f"     {gone} passages out, {windows} in")
    episode_store.merge([{**row, "title": title, "subject": subject,
                          "seconds": int(max(s["t"] for s in kept)),
                          "segments": kept}], path=SHELF)
    print("  repack for the image:  python scripts/pack_episodes.py "
          "data/tradfi_episodes.json")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="YouTube URLs to add")
    ap.add_argument("--subject", default="",
                    help="who is speaking, e.g. 'Larry Fink' — recorded on "
                         "the shelf so the archive can grow past one person")
    ap.add_argument("--list", action="store_true",
                    help="show what is already indexed and stop")
    ap.add_argument("--redo", metavar="VIDEO_ID",
                    help="index a shelved recording again from its own "
                         "transcript, cleaned, under --subject if given")
    args = ap.parse_args()

    if args.redo:
        return await redo(args.redo, args.subject)

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
        kept, labels = strip_speaker_labels(kept)
        if labels:
            print(f"     removed speaker labels nobody said: {', '.join(labels)}")
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
            # The clipper decides X broadcast vs YouTube from this,
            # and without it every finance clip was captioned "X
            # broadcast" and carried a note saying the moment was
            # not in the YouTube upload, on a YouTube video.
            "platform": "youtube",
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

"""Summaries for MCG's episodes, written once and shipped.

    .venv/bin/python scripts/summarize_mcg.py --latest 2 --dry-run
    .venv/bin/python scripts/summarize_mcg.py --only 4eErwRiL8VE --force
    .venv/bin/python scripts/summarize_mcg.py --redo --transcripts ~/mcg-search/data/episodes.json

A TL;DR and the topics in order, each with the second it starts at, written
to data/mcg_summaries.json.gz, which the server reads and the image ships.
The 404 on file were written when MCG Search was its own service, by its
scripts/summarize.py, and copied here on 6 Sep 2026; this is their writer
now, with the checks scripts/summarize_threadguy.py holds every topic to.

Those 404 asked the model for each topic's start in seconds, and it worked
them out from the printed timestamps itself. Measured on 28 Oct 2026
against the transcripts they were written from: a quarter of the topics
sit on a line the transcript has (by chance alone it would be one in
seven), one in ten is a whole minute, the error grows the further into an
episode a topic is (more than ten minutes late for 18% of topics between
minute 20 and 40, 27% between 40 and 60), and on Arrow Finance's episode
every topic was written in minutes, so all nine played from the first
forty seconds. Here the model copies the timestamp and this does the sum.

The transcript is the one each summary is checked against, so it has to be
the one the page plays: --transcripts reads a file of them with every
line's time (MCG Search's data/episodes.json is the 404's own), which reads
nothing from Pinecone; without it each one is rebuilt from the index, a
metadata query per episode.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anthropic import AsyncAnthropic  # noqa: E402

from app import mcg_transcript  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from scripts import summarize_threadguy as held  # noqa: E402

SHELF = ROOT / "data" / "mcg_index.json"
OUT = ROOT / "data" / "mcg_summaries.json.gz"

SYSTEM = """\
You are summarising one episode of the MCG podcast, which interviews one \
crypto project per episode. Every transcript line starts with its \
[h:mm:ss] timestamp.

Return ONLY a JSON object, no prose around it, shaped exactly like this:

{"tldr": "...", "topics": [{"at": "0:02:05", "text": "..."}, ...]}

tldr: two or three sentences. What the project does, who is talking, and \
the single most interesting or surprising thing in the conversation. \
Write it for somebody deciding whether to spend forty minutes here, not \
as a description of a video. Name the project. Do not open with "In this \
episode" or "This episode covers".

topics: between five and ten entries, in the order they occur.
  at    the timestamp of the line the topic begins at, copied exactly as \
it is printed in front of that line, like 0:12:05. A reader presses it and \
expects to land on that line. If you are not certain which line a topic \
starts at, leave the topic out.
  text  one sentence, specific. "The fee split: 1% of volume, 0.35% to \
the platform" not "They discuss fees". Numbers, names and claims are the \
whole value, and they are worth it only as the transcript has them.

Rules:
- Use ONLY the transcript. Do not add anything you know about the project \
from elsewhere.
- These are auto-generated captions with no speaker labels, and an \
interview has at least two people in it. Say "the founder" or "the host" \
unless the transcript itself makes a name unambiguous. Being ADDRESSED \
is not a name: "dude", "bro", "man", "boss", "guys" are how people talk \
to each other, and one summary opened "Dude, founder of RatSpeak" \
because the transcript had somebody say it. Unusual project \
names are often transcribed wrongly; use the spelling from the episode \
title, which is human-written.
- This is informational, never investment advice. Report what was \
claimed, attributed to whoever claimed it. Never endorse a project, \
assess a token, or repeat a projection as though it were a fact. No em \
dashes.
- If the transcript is too broken or too short to summarise honestly, \
return {"tldr": "", "topics": []} rather than inventing something."""

# Words that are how people address each other, not names. A TL;DR that
# misnames the founder in its first word is worse than one that says "the
# founder": it reads as confidently wrong.
_NOT_A_NAME = re.compile(r"^(dude|bro|man|guys|buddy|boss|sir|mate|yeah|okay|hey)\b",
                         re.IGNORECASE)


def looks_misattributed(tldr: str) -> bool:
    return bool(_NOT_A_NAME.match((tldr or "").strip()))


def transcripts_from(path: Path) -> dict[str, list[dict]]:
    """Episode id -> its timed lines, from a file of episodes shaped like
    MCG Search's: [{"episode_id", "segments": [{"t", "text"}]}]."""
    rows = json.loads(path.expanduser().read_text())
    return {r["episode_id"]: [{"t": float(s["t"]), "text": s["text"]}
                              for s in r.get("segments") or []]
            for r in rows if r.get("episode_id")}


async def summarize(client, model: str, row: dict, segments: list[dict]) -> dict:
    summary = await held.summarize(client, model, row, segments, system=SYSTEM,
                                   given=row.get("title", ""))
    if looks_misattributed(summary["tldr"]):
        raise ValueError(f"the tldr opens on a word that is not a name: "
                         f"{summary['tldr'][:40]!r}")
    return summary


def load() -> dict:
    if not OUT.exists():
        return {}
    with gzip.open(OUT, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def save(store: dict) -> None:
    body = json.dumps(store, ensure_ascii=False, separators=(",", ":"))
    tmp = OUT.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(body.encode(), mtime=0))
    tmp.replace(OUT)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", type=int, default=40,
                    help="summarize the newest N episodes on the shelf")
    ap.add_argument("--only", action="append", default=[], help="one episode id")
    ap.add_argument("--force", action="store_true", help="redo existing ones")
    ap.add_argument("--redo", action="store_true",
                    help="write every summary on file again, keeping the old "
                         "one where the new one fails")
    ap.add_argument("--transcripts", type=Path, default=None,
                    help="a file of timed transcripts to read instead of the index")
    ap.add_argument("--dry-run", action="store_true", help="print, store nothing")
    ap.add_argument("--model", default=None,
                    help="defaults to SUMMARY_MODEL, the one Market Bubble uses")
    args = ap.parse_args()

    settings = get_settings()
    model = args.model or settings.summary_model
    rows = json.loads(SHELF.read_text())
    rows = rows if isinstance(rows, list) else list(rows.values())
    rows.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    store = load()
    if args.only:
        todo = [r for r in rows if r.get("id") in set(args.only)]
    elif args.redo:
        todo = [r for r in rows if r.get("id") in store]
    else:
        todo = rows[:args.latest]

    on_disk = transcripts_from(args.transcripts) if args.transcripts else None
    index = None
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    made = 0
    failed: list[str] = []
    for row in todo:
        vid = row.get("id")
        if vid in store and not (args.force or args.redo):
            continue
        started = time.monotonic()
        try:
            if on_disk is not None:
                segments = on_disk.get(vid) or []
            else:
                if index is None:
                    index = PodcastIndex(namespace=settings.mcg_namespace,
                                         index_name=settings.mcg_pinecone_index).index
                segments = await asyncio.to_thread(
                    mcg_transcript.rebuild, index, settings.mcg_namespace,
                    settings.embedding_dimension, vid)
            if not segments:
                raise ValueError("no transcript for it")
            summary = await summarize(client, model, row, segments)
        except Exception as exc:                              # noqa: BLE001
            failed.append(vid)
            kept = " (the old one stays)" if vid in store else ""
            print(f"  x {vid}  {row.get('title', '')[:60]}: {str(exc)[:160]}{kept}")
            continue
        dropped = summary.pop("dropped")
        summary["model"] = model
        print(f"  + {vid}  {int(time.monotonic() - started)}s  "
              f"{len(summary['topics'])} topics  {row.get('title', '')[:60]}")
        for problem in dropped:
            print(f"      dropped a topic: {problem[:150]}")
        if args.dry_run:
            print(json.dumps(summary, indent=1, ensure_ascii=False)[:1500])
            continue
        store[vid] = summary
        save(store)
        made += 1
    print(f"\n  {made} written, {len(failed)} failed, {len(store)} on file")
    if failed:
        print(f"  failed: {' '.join(failed)}")
    return 1 if failed and not made and not args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

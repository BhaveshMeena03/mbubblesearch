"""Summaries for the newest ThreadGuy episodes, written once and shipped.

    .venv/bin/python scripts/summarize_threadguy.py --latest 40
    .venv/bin/python scripts/summarize_threadguy.py --latest 2 --dry-run
    .venv/bin/python scripts/summarize_threadguy.py --only 2tMMfjBqgvo --force

The same shape as MCG's: a two or three sentence summary and the topics in
order, each with the second it starts at, so the shelf can show what an
episode covered and play any part of it. Written to
data/threadguy_summaries.json.gz, which the server reads and the image
ships, exactly like data/mcg_summaries.json.gz.

The transcript comes back from the episode's own vectors (this archive keeps
no transcripts on disk), with a timestamp on every line. One model call per
episode, through the proxy, streamed: a three hour market open is fifty
thousand tokens, and a long call held open in silence is what timed out
through the proxy when Market Bubble's summaries were first written.

Newest first and idempotent: an episode already summarized is skipped, so
re-running after new uploads pays only for those.
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
from scripts.ingest_mcg import shortfall  # noqa: E402

SHELF = ROOT / "data" / "threadguy_index.json"
# Closer than this and two topics are one topic told twice, or the model
# pinning both to the same stretch because it lost its place.
MIN_GAP = 90
OUT = ROOT / "data" / "threadguy_summaries.json.gz"

SYSTEM = """\
You summarize episodes of ThreadGuy's YouTube channel from their \
transcripts: the daily live market open, and interviews with guests. Every \
transcript line starts with its [h:mm:ss] timestamp.

ThreadGuy hosts every episode. The transcript carries no speaker labels and \
his name is almost never in it, because he is the one talking. On a market \
open, the views are his unless the line plainly belongs to someone else (a \
guest who joined, a clip being played, chat being read out). In an \
interview, the guest is named in the title; attribute a view to the guest \
by name only when the line is plainly the guest answering the host. When \
you cannot tell who is speaking, say "on the stream" rather than guessing.

Write only what the transcript says. Do not add context from outside it, \
do not invent quotes, and do not relay any buy or sell call as a \
recommendation; describe it as what was said on the day. Plain words, no \
hype, no em dashes.

Reply with JSON only, no prose around it, in exactly this shape:
{"tldr": "two or three sentences: what the episode covers and its single \
most interesting thread",
 "topics": [{"t": <seconds where the topic starts, an integer taken from \
the timestamp of the line it starts on>, "text": "one sentence on what was \
said, specific: names, numbers, positions"}]}

Give 6 to 12 topics in the order they come up, spread across the whole \
episode, each starting on a real line. Leave out what is not content: \
intro music, waiting for the stream to start, reading out who is in chat, \
sponsor reads, audio trouble. A topic is something said about a market, a \
project, a person or an idea, and two topics are a few minutes apart unless \
the conversation really does change faster than that."""


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcript(segments: list[dict]) -> str:
    return "\n".join(f"[{stamp(x['t'])}] {x['text']}" for x in segments)


def plain(text: str) -> str:
    """No em or en dashes in anything the site shows."""
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def parse(raw: str, duration: float, starts: list[float]) -> dict:
    """The model's reply, checked before it is allowed onto the page.

    Raises ValueError on anything malformed rather than storing a guess: a
    summary with a topic at a second the episode does not have is a link
    that lands nowhere, on the one feature that promises the second.
    """
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("no JSON object in the reply")
    data = json.loads(match.group(0))
    tldr = plain(str(data.get("tldr") or ""))
    if not 40 <= len(tldr) <= 900:
        raise ValueError(f"tldr of {len(tldr)} characters")
    topics = []
    for item in data.get("topics") or []:
        try:
            t = float(item["t"])
        except (KeyError, TypeError, ValueError):
            continue
        text = plain(str(item.get("text") or ""))
        if not text or t < 0 or t > duration + 5:
            continue
        # Onto the line it starts on: the latest line at or before t.
        line = max((s for s in starts if s <= t), default=starts[0])
        topics.append({"t": int(line), "text": text})
    topics.sort(key=lambda x: x["t"])
    spaced: list[dict] = []
    for topic in topics:
        if spaced and topic["t"] - spaced[-1]["t"] < MIN_GAP:
            continue
        spaced.append(topic)
    topics = spaced
    if len(topics) < 4:
        raise ValueError(f"only {len(topics)} usable topics")
    return {"tldr": tldr, "topics": topics[:12]}


async def summarize(client, model: str, row: dict, segments: list[dict]) -> dict:
    duration = float(row.get("seconds") or segments[-1]["t"])
    # The fourteen streams shelved half transcribed taught this: a summary
    # of the first six minutes of a three hour stream reads as a summary
    # of the stream. Better none than that.
    short = shortfall(segments, duration)
    if short:
        raise ValueError(f"incomplete transcript: {short}")
    user = (f"Episode: {row.get('title', '')}\n"
            f"Published: {row.get('published_at', '')}\n"
            f"Format: {row.get('format', '')}\n\n"
            f"<transcript>\n{transcript(segments)}\n</transcript>")
    async with client.messages.stream(
            model=model, max_tokens=3000, system=SYSTEM,
            messages=[{"role": "user", "content": user}]) as stream:
        message = await stream.get_final_message()
    raw = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    return parse(raw, duration, [float(x["t"]) for x in segments])


def load() -> dict:
    if not OUT.exists():
        return {}
    with gzip.open(OUT, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def save(store: dict) -> None:
    body = json.dumps(store, ensure_ascii=False, separators=(",", ":"))
    OUT.write_bytes(gzip.compress(body.encode(), mtime=0))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", type=int, default=40,
                    help="summarize the newest N episodes on the shelf")
    ap.add_argument("--only", action="append", default=[], help="one episode id")
    ap.add_argument("--force", action="store_true", help="redo existing ones")
    ap.add_argument("--dry-run", action="store_true", help="print, store nothing")
    ap.add_argument("--model", default=None,
                    help="defaults to SUMMARY_MODEL, the one Market Bubble uses")
    args = ap.parse_args()

    settings = get_settings()
    model = args.model or settings.summary_model
    rows = json.loads(SHELF.read_text())
    rows = rows if isinstance(rows, list) else list(rows.values())
    rows.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    todo = ([r for r in rows if r.get("id") in set(args.only)] if args.only
            else rows[:args.latest])

    store = load()
    index = PodcastIndex(namespace=settings.threadguy_namespace).index
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    made = failed = 0
    for row in todo:
        vid = row.get("id")
        if vid in store and not args.force:
            continue
        started = time.monotonic()
        try:
            segments = await asyncio.to_thread(
                mcg_transcript.rebuild, index, settings.threadguy_namespace,
                settings.embedding_dimension, vid)
            if not segments:
                raise ValueError("no transcript in the index")
            summary = await summarize(client, model, row, segments)
        except Exception as exc:                              # noqa: BLE001
            failed += 1
            print(f"  x {vid}  {row.get('title', '')[:60]}: {str(exc)[:160]}")
            continue
        summary.update(title=row.get("title", ""),
                       published_at=row.get("published_at", ""), model=model)
        print(f"  + {vid}  {int(time.monotonic() - started)}s  "
              f"{len(summary['topics'])} topics  {row.get('title', '')[:60]}")
        if args.dry_run:
            print(json.dumps(summary, indent=1, ensure_ascii=False)[:1500])
            continue
        store[vid] = summary
        save(store)
        made += 1
    print(f"\n  {made} written, {failed} failed, {len(store)} on file")
    return 1 if failed and not made else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

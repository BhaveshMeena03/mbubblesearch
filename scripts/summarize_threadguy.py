"""Summaries for the newest ThreadGuy episodes, written once and shipped.

    .venv/bin/python scripts/summarize_threadguy.py --latest 40
    .venv/bin/python scripts/summarize_threadguy.py --latest 2 --dry-run
    .venv/bin/python scripts/summarize_threadguy.py --only 2tMMfjBqgvo --force
    .venv/bin/python scripts/summarize_threadguy.py --redo

The same shape as MCG's (scripts/summarize_mcg.py, which reuses the checks
here): a two or three sentence summary and the topics in order, each with
the second it starts at, so the shelf can show what an episode covered and
play any part of it. Written to data/threadguy_summaries.json.gz, which the
server reads and the image ships, exactly like data/mcg_summaries.json.gz.

The transcript comes back from the episode's own vectors (this archive keeps
no transcripts on disk), with a timestamp on every line. One model call per
episode, through the proxy, streamed: a three hour market open is fifty
thousand tokens, and a long call held open in silence is what timed out
through the proxy when Market Bubble's summaries were first written.

Every topic is held to the transcript before it is stored. The model copies
the timestamp printed in front of the line a topic starts on and this script
turns it into seconds; the time has to be a line the transcript has, and the
topic's numbers and names have to be in the transcript near it. Asked for
seconds instead, the model did the sum itself and got it wrong, later the
further into the stream it went: on the 22 Oct 2026 market open the
Cloudflare CEO's numbers, read at 0:42:52, were stored at 1:01:38, and the
guest's certificate bug, at 1:42:36, at 2:15:58.

Newest first and idempotent: an episode already summarized is skipped, so
re-running after new uploads pays only for those. --redo writes every
summary on file again, and keeps the old one where the new one fails.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
import httpx  # noqa: E402
from anthropic import AsyncAnthropic  # noqa: E402

from app import mcg_transcript, names  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from scripts.ingest_mcg import shortfall  # noqa: E402

SHELF = ROOT / "data" / "threadguy_index.json"
# Closer than this and two topics are one topic told twice, or the model
# pinning both to the same stretch because it lost its place.
MIN_GAP = 90
OUT = ROOT / "data" / "threadguy_summaries.json.gz"
# He is the one talking, so his name is almost never in the transcript,
# and every summary uses it.
HOST = "ThreadGuy"

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
recommendation; describe it as what was said on the day. Numbers, names \
and tickers exactly as the transcript has them. Plain words, no hype, no \
em dashes.

Reply with JSON only, no prose around it, in exactly this shape:
{"tldr": "two or three sentences: what the episode covers and its single \
most interesting thread",
 "topics": [{"at": "the timestamp of the line the topic starts on, copied \
exactly as it is printed in front of that line, like 1:04:28", "text": "one \
sentence on what was said, specific: names, numbers, positions"}]}

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


# --- what a topic may claim -------------------------------------------------

_STAMP = re.compile(r"^\[?(\d+):(\d{2}):(\d{2})\]?$")
_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)(?:\s?(k|mm|m|bn|b|thousand|million|billion)\b)?",
                     re.I)
_SCALE = {"k": 3, "thousand": 3, "m": 6, "mm": 6, "million": 6,
          "b": 9, "bn": 9, "billion": 9}
# Capitalised words a summary uses that are not names from the episode.
_ORDINARY = set("""The This That These Those With From Into Over Under After Before While When
Where What Which Their There They Then Than Also Plus Both Each Most Some Many More Less
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April June
July August September October November December Chart Markets Market Open Close Headlines
Hosts Host Guest Guests Founder Founders Team Project Token Episode Interview Live Day Week
Year Show Stream""".split())


def seconds(stamp: str) -> int | None:
    """The model's copy of a printed timestamp, as seconds. Only the h:mm:ss
    the transcript prints: anything else is not a copy."""
    found = _STAMP.match(stamp.strip())
    if not found:
        return None
    h, m, s = (int(x) for x in found.groups())
    return h * 3600 + m * 60 + s


def _value(digits: str) -> str:
    digits = digits.replace(",", "").rstrip(".")
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    return digits


def _forms(text: str) -> list[set[str]]:
    """Each number in text, as every value it can stand for: "6,182" is
    6182, "29.50" is 29.5, and "$360K" is 360 or 360000, whichever way the
    other side happened to write it."""
    out = []
    for digits, scale in _NUMBER.findall(text):
        bare = _value(digits)
        if not bare:
            continue
        forms = {bare}
        if scale:
            try:
                forms.add(_value(f"{Decimal(bare).scaleb(_SCALE[scale.lower()]):f}"))
            except InvalidOperation:
                pass
        out.append(forms)
    return out


def _numbers(text: str) -> set[str]:
    return {form for forms in _forms(text) for form in forms}


def _names(text: str) -> set[str]:
    """Capitalised words a summary would only use for a name, product or
    place. A hyphenated word is its parts: "Satoshi-era" checks Satoshi,
    "AI-discoverable" checks nothing (AI is too short to tell)."""
    words = re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z'-]{3,})", text)
    parts = {p for w in words for p in w.split("-")}
    return {p for p in parts if len(p) >= 4 and p[0].isupper() and p not in _ORDINARY}


def _said(word: str, said: str) -> bool:
    """word in said as a whole word, plural or possessive, or split once by
    a space the way captions split names: "chat gpt", "robin hood"."""
    bare = re.sub(r"(?:['’]s?|s)$", "", word).lower()
    ways = [re.escape(bare)] + [f"{re.escape(bare[:k])}\\s{re.escape(bare[k:])}"
                                for k in range(2, len(bare) - 1)]
    return bool(re.search(rf"\b(?:{'|'.join(ways)})(?:s|'s|’s)?\b", said))


def missing_numbers(text: str, said: str) -> list[str]:
    """Numbers in text the transcript (said, lowercase) does not have."""
    have = _numbers(said)
    return sorted(min(forms, key=len) for forms in _forms(text) if not forms & have)


def missing_names(text: str, said: str) -> list[str]:
    """Names in text the transcript does not have."""
    return [word for word in sorted(_names(text)) if not _said(word, said)]


def unsupported(text: str, said: str) -> list[str]:
    return missing_numbers(text, said) + missing_names(text, said)


# Near enough to be the same stretch. A figure belongs to the minutes it
# was said in; a guest's name is said when they come on and often not
# again, so a topic twenty minutes in looks back that far for it.
FIGURES_BEFORE, NAMES_BEFORE, AFTER = 300, 1800, 900
# A copied timestamp a few seconds off a real line ("1:04:35" for the line
# at 1:04:39) is that line; the checks above still have to pass there.
SNAP = 15


def _near(segments: list[dict], t: int, before: int) -> str:
    return " ".join(x["text"] for x in segments if t - before <= x["t"] <= t + AFTER).lower()


def check(raw: str, segments: list[dict], keep_what_passes: bool,
          given: str = "") -> tuple[dict, list[str]]:
    """The model's reply, held to the transcript before it may be stored.

    Raises ValueError when it cannot be used. Every topic must start on a
    line the transcript has, at the time that line is printed with, and
    every number and name in it must be in the transcript, near that time
    (so a topic cannot sit on the wrong stretch). given is what a summary
    may say that the transcript need not: the title and the host's name.
    With keep_what_passes, a failing topic is dropped instead of failing
    the whole reply; the tldr is never let off."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("no JSON object in the reply")
    data = json.loads(match.group(0))
    given = names.fix(given)[0].lower()
    said = " ".join(x["text"] for x in segments).lower() + " " + given
    starts = sorted({int(x["t"]) for x in segments})
    tldr = plain(str(data.get("tldr") or ""))
    if not 40 <= len(tldr) <= 900:
        raise ValueError(f"tldr of {len(tldr)} characters")
    bad = unsupported(tldr, said)
    if bad:
        raise ValueError(f"tldr has what the transcript does not: {bad}")
    topics, dropped = [], []
    for item in data.get("topics") or []:
        text = plain(str(item.get("text") or ""))
        t = seconds(str(item.get("at") or ""))
        if t is not None:
            close = [s for s in starts if abs(s - t) <= SNAP]
            t = min(close, key=lambda s: abs(s - t)) if close else None
        if not text or t is None:
            problem = f"no line at {item.get('at')!r}"
        else:
            missing = (unsupported(text, said)
                       or missing_numbers(text, _near(segments, t, FIGURES_BEFORE) + " " + given)
                       + missing_names(text, _near(segments, t, NAMES_BEFORE) + " " + given))
            problem = f"not in the transcript near {item.get('at')}: {missing}" if missing else ""
        if problem:
            if not keep_what_passes:
                raise ValueError(problem)
            dropped.append(problem)
            continue
        topics.append({"t": t, "text": text})
    topics.sort(key=lambda x: x["t"])
    spaced: list[dict] = []
    for topic in topics:
        if not spaced or topic["t"] - spaced[-1]["t"] >= MIN_GAP:
            spaced.append(topic)
    if len(spaced) < 4:
        raise ValueError(f"only {len(spaced)} usable topics")
    return {"tldr": tldr, "topics": spaced[:12]}, dropped


async def ask(client, model: str, user: str, system: str = SYSTEM):
    """One call, streamed. The proxy has cut long summary calls before, so
    a dropped connection is tried again after a pause, three times in all."""
    for wait in (30, 90, None):
        try:
            async with client.messages.stream(
                    model=model, max_tokens=3000, system=system,
                    messages=[{"role": "user", "content": user}]) as stream:
                return await stream.get_final_message()
        # The proxy also drops a stream partway: "peer closed connection
        # without sending complete message body", straight from httpx.
        except (anthropic.APITimeoutError, anthropic.APIConnectionError,
                anthropic.InternalServerError, httpx.TransportError) as exc:
            if wait is None:
                raise
            print(f"      the call dropped ({type(exc).__name__}); again in {wait}s")
            await asyncio.sleep(wait)


async def summarize(client, model: str, row: dict, segments: list[dict],
                    system: str = SYSTEM, given: str | None = None) -> dict:
    """Two tries at most. The first reply must pass whole; the second keeps
    the topics that pass. A reply that is not valid JSON (an unescaped
    quote in a quotation) is the commonest failure and a second try clears
    it. The topics a second try dropped come back under "dropped", for the
    log; they are not stored."""
    duration = float(row.get("seconds") or segments[-1]["t"])
    # The fourteen streams shelved half transcribed taught this: a summary
    # of the first six minutes of a three hour stream reads as a summary
    # of the stream. Better none than that.
    short = shortfall(segments, duration)
    if short:
        raise ValueError(f"incomplete transcript: {short}")
    # The spellings the rest of the site uses ("Salana" is Solana), so the
    # model reads them right and its names can be checked against them.
    segments = [{"t": x["t"], "text": names.fix(x["text"])[0]} for x in segments]
    if given is None:
        given = f"{HOST} {row.get('title', '')}"
    user = (f"Episode: {row.get('title', '')}\n"
            f"Published: {row.get('published_at', '')}\n"
            f"Format: {row.get('format', '')}\n\n"
            f"<transcript>\n{transcript(segments)}\n</transcript>")
    last = ""
    for attempt in (1, 2):
        message = await ask(client, model, user, system)
        raw = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        try:
            summary, dropped = check(raw, segments, keep_what_passes=attempt == 2,
                                     given=given)
        except ValueError as exc:
            last = str(exc)
            print(f"      try {attempt} not used: {last[:150]}")
            continue
        summary["dropped"] = dropped
        return summary
    raise ValueError(f"no usable summary in two tries: {last[:200]}")


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

    index = PodcastIndex(namespace=settings.threadguy_namespace).index
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    made = 0
    failed: list[str] = []
    for row in todo:
        vid = row.get("id")
        if vid in store and not (args.force or args.redo):
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
            failed.append(vid)
            kept = " (the old one stays)" if vid in store else ""
            print(f"  x {vid}  {row.get('title', '')[:60]}: {str(exc)[:160]}{kept}")
            continue
        dropped = summary.pop("dropped")
        summary.update(title=row.get("title", ""),
                       published_at=row.get("published_at", ""), model=model)
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

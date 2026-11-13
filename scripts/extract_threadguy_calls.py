"""Find call candidates in ThreadGuy's solo streams.

    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_threadguy_calls.py --all
    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_threadguy_calls.py --episode <youtube id>

Runs the Market Bubble extractor (extract_calls.py) over his streams with
the same checks from app/calls.py. Streams with a guest in the title and his
interviews are skipped. There are no speaker labels, so every line counts as
his here and each kept row gets checked on X later.

Reads lines from the local cache (app/local_cache.py), not Pinecone.
Writes data/threadguy_calls_candidates.json.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anthropic import AsyncAnthropic  # noqa: E402

from app import calls, local_cache  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings  # noqa: E402

_spec = importlib.util.spec_from_file_location("extract_calls", ROOT / "scripts" / "extract_calls.py")
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)

HOST = "ThreadGuy"
HOSTS = (HOST,)
SHELF = ROOT / "data" / "threadguy_index.json"
CACHE = ROOT / "data" / ".calls_cache" / "threadguy"
OUT = ROOT / "data" / "threadguy_calls_candidates.json"
# bump to re-read every stream after changing the prompt
PROMPT_VERSION = 1
# streams with a guest say so in the title
GUESTED = re.compile(r"interview|\bw/|\bwith\b|\bft\.?\s|\bfeat|\bguest|joined by", re.I)

TOOL = json.loads(json.dumps(mb.TOOL))
TOOL["input_schema"]["properties"]["calls"]["items"]["properties"]["speaker"]["enum"] = [HOST]
TOOL["description"] = "Record every call ThreadGuy makes in this excerpt."

SYSTEM = """\
You read excerpts of ThreadGuy's live stream, a show about markets that he \
hosts alone at his desk, and record the CALLS he makes.

Each line is written as: number | time | voice | words. Every line is \
marked ThreadGuy, because he is the only one at the desk. The transcript \
is automatic: little punctuation, and names are often misheard ("soxel" \
for SOXL, "hype" for Hyperliquid, "z cash" for Zcash).

A call is ThreadGuy putting HIMSELF on record about ONE specific tradeable \
asset. There are four kinds:
- holding: he says he owns it, is long or short it, bought it or is buying it.
- pick: he names it as what he would buy, his pick, his highest conviction.
- target: he predicts a specific price or market cap for it.
- exit: he says he sold it or closed the position.

Record a call only when ALL of this is true:
- They are his own words. He reads tweets, headlines, chat messages and \
other people's posts out loud all the time: "this guy says Bitcoin could \
go to zero", "chat says buy the dip", "Flood tweeted that he is long". \
Whatever he reads out, or reports someone else saying or owning, is NOT \
his call, even in the first person.
- He is speaking for himself, now. Not describing what someone else owns \
or thinks, not a question, not a joke, not a hypothetical, not a story \
about a trade from long ago.
- It is a view he would stand behind, not a trade. Not a hedge, not a \
position taken off the chart for a few days, not a scalp or an intraday \
trade, not a setup with an entry, a stop and a target.
- It is one named asset: a token, a stock, an ETF, a commodity. Not a \
category ("alts", "AI stocks", "memes", "semis"), not a prediction-market \
bet, not a sponsor.
- The quote is 8 to 40 consecutive words copied EXACTLY as written in the \
lines, filler words included, and the asset is clear from the quote or the \
line just before it.

Most excerpts contain no calls. Return an empty list rather than stretch. \
Describing an asset, reading its price, or saying a chart looks good is not \
a call unless he says he owns it, would buy it, or puts a number on it.\
"""


def streams(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Solo streams (oldest first) and the ones skipped for a guest."""
    solo, guested = [], []
    for row in rows:
        if row.get("format") != "stream":
            continue
        (guested if GUESTED.search(str(row.get("title") or "")) else solo).append(row)
    key = lambda r: str(r.get("published_at") or "")  # noqa: E731
    return sorted(solo, key=key), sorted(guested, key=key)


def as_episode(row: dict, lines: list[dict]) -> tuple[dict, dict[str, str]]:
    """Stream in the shape calls.py expects, every line labelled ThreadGuy."""
    episode = {"episode_id": row["id"], "title": row.get("title"),
               "published_at": row.get("published_at"), "segments": lines}
    return episode, {str(i): HOST for i in range(len(lines))}


async def read_stream(client, model: str, episode: dict, labels: dict[str, str],
                      force: bool) -> list[dict]:
    """Model output for one stream, cached by prompt version and line count."""
    CACHE.mkdir(parents=True, exist_ok=True)
    kept = CACHE / f"{episode['episode_id']}.json"
    if kept.exists() and not force:
        saved = json.loads(kept.read_text())
        if (saved.get("prompt") == PROMPT_VERSION
                and saved.get("lines") == len(episode["segments"])):
            return saved["raw"]
    parts = mb.windows(episode["segments"], labels)
    gate = asyncio.Semaphore(mb.MAX_CONCURRENCY)
    found = await asyncio.gather(*(mb.ask(client, model, w, gate, system=SYSTEM, tool=TOOL)
                                   for w in parts))
    raw = [claim for part in found for claim in part if isinstance(claim, dict)]
    kept.write_text(json.dumps({"prompt": PROMPT_VERSION, "lines": len(episode["segments"]),
                                "windows": len(parts), "raw": raw}, indent=1))
    return raw


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--episode", action="append", default=[], help="a stream's YouTube id")
    ap.add_argument("--all", action="store_true", help="every solo stream")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    args = ap.parse_args()
    if not args.all and not args.episode:
        ap.error("pass --episode ID or --all")

    rows = json.loads(SHELF.read_text())
    rows = rows if isinstance(rows, list) else list(rows.values())
    solo, guested = streams(rows)
    todo = [r for r in solo if not args.episode or r["id"] in set(args.episode)]
    settings = get_settings()
    model = os.environ.get("CALLS_MODEL", settings.search_model)
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))

    before = json.loads(OUT.read_text()) if OUT.exists() else {"shows": {}}
    report = dict(before.get("shows", {}))
    missing = []
    for row in todo:
        lines = local_cache.load_lines("threadguy", row["id"])
        if not lines:
            missing.append(row["id"])
            continue
        episode, labels = as_episode(row, lines)
        raw = await read_stream(client, model, episode, labels, args.force)
        refused: Counter = Counter()
        passed = []
        for claim in raw:
            call, why = calls.accept(claim, episode, labels, HOSTS)
            if call is None:
                refused[why] += 1
            else:
                passed.append(call)
        kept = calls.one_per_show(passed)
        report[row["id"]] = {"title": row.get("title"), "published_at": row.get("published_at"),
                             "proposed": len(raw), "refused": dict(refused),
                             "candidates": [c.as_row() for c in kept]}
        print(f"  {str(row.get('published_at'))[:10]}  {len(raw):3} proposed  {len(kept):3} kept  "
              f"{dict(refused)}  {str(row.get('title'))[:44]}", flush=True)
    OUT.write_text(json.dumps({"shows": report, "left_out_for_a_guest":
                               [{"id": r["id"], "title": r.get("title")} for r in guested]},
                              indent=1, ensure_ascii=False))
    total = sum(len(s["candidates"]) for s in report.values())
    print(f"\n{total} candidates across {len(report)} streams -> {OUT.relative_to(ROOT)}")
    if missing:
        print(f"{len(missing)} streams have no cached lines and were not read: {missing[:10]}")
    print(f"{len(guested)} streams left out for a guest")
    if mb.USAGE:
        print(f"model: {mb.USAGE['requests']} requests, {mb.USAGE['input_tokens']:,} in, "
              f"{mb.USAGE['output_tokens']:,} out")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

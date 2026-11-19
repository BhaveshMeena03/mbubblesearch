"""Find the price levels the hosts named on air.

    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_levels.py --episode x-2115904746355851479
    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_levels.py --all

Reads the live shows the way scripts/extract_calls.py does (numbered,
voice-marked lines, a few minutes at a time) and asks for a different
thing: a price a host put on one asset. Each proposal is held against
the transcript by app/levels.py, which refuses anything whose quote is
not there word for word, whose line is not in a host's voice, or whose
number is not in the quote.

What comes out is data/levels_candidates.json. A person reads it and
records a verdict on each in data/levels_verdicts.json, and
scripts/build_levels.py builds data/levels.json from the ones kept.

Windows with no digit in anything said are not sent: a level is a
number, and the captions write numbers as digits.

Ansem's own videos ("Ansem on X") have no voice labels, because nobody
else talks in them; every line is read as his (app/levels.py voices).

The model's raw replies are cached per show in data/.levels_cache/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from anthropic import AsyncAnthropic  # noqa: E402
from extract_calls import USAGE, ask, has_a_host, live_shows, windows  # noqa: E402

from app import calls, levels  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings  # noqa: E402

EPISODES = ROOT / "data" / "episodes.json"
LABELS = ROOT / "data" / "speaker_map.json"
CACHE = ROOT / "data" / ".levels_cache"
OUT = ROOT / "data" / "levels_candidates.json"
MAX_CONCURRENCY = int(os.environ.get("LEVELS_CONCURRENCY", "2"))
# Bump when SYSTEM changes and every show should be read again.
PROMPT_VERSION = 1

TOOL = {
    "name": "record_levels",
    "description": "Record every price level a host names in this excerpt.",
    "input_schema": {
        "type": "object",
        "properties": {
            "levels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "integer",
                                 "description": "The number of the line the quote starts on."},
                        "speaker": {"type": "string", "enum": list(calls.HOSTS),
                                    "description": "The voice marked on that line."},
                        "asset": {"type": "string",
                                  "description": "The asset's name, e.g. Bitcoin, Zcash, Nvidia."},
                        "symbol": {"type": "string",
                                   "description": "Its ticker in caps, e.g. BTC, ZEC, NVDA."},
                        "asset_class": {"type": "string",
                                        "enum": ["crypto", "stock", "index", "commodity", "other"]},
                        "kind": {"type": "string", "enum": list(levels.KINDS)},
                        "level": {"type": "number",
                                  "description": "The full price in US dollars: 65000 for 'bitcoin goes to like 65', 750 for 'zcash at 750'."},
                        "quote": {"type": "string",
                                  "description": "8 to 40 consecutive words copied exactly from the lines, filler included, containing the number."},
                    },
                    "required": ["line", "speaker", "asset", "symbol", "asset_class",
                                 "kind", "level", "quote"],
                },
            }
        },
        "required": ["levels"],
    },
}

SYSTEM = """\
You read excerpts of Market Bubble, a live show about markets hosted by \
Ansem and FaZe Banks, and record the PRICE LEVELS the two hosts name.

Each line is written as: number | time | voice | words. The voice is \
"Ansem", "FaZe Banks" or "?" when it is not known (guests and crosstalk \
are "?"). The transcript is automatic: no punctuation, and names are often \
misheard ("z cash", "soul", "hype" for Hyperliquid).

A level is a host putting a specific PRICE, as a number, on ONE specific \
tradeable asset. There are four kinds:
- buy: a price he would buy or add at ("I want to buy Zcash at like 750").
- sell: a price he would sell or take profit at.
- target: a price he expects it to reach, up or down ("Bitcoin goes to \
like 65", "ETH to 10k this cycle").
- line: a price he says decides something: a level it has to hold, or \
whose loss or reclaim changes his view ("if we lose 100k it's over").

Record a level only when ALL of this is true:
- The line the quote starts on is marked Ansem or FaZe Banks. Never record \
one from a "?" line, however sure you are who is talking.
- It is his own view of where the price goes or what he would do there. \
NOT the price right now ("bitcoin's at 78k"), not where it was, not what \
somebody else thinks, not a question, not a joke.
- It is a price for one named asset. Not a market cap or an FDV, not a \
percentage, not a ratio between two assets.
- The quote is 8 to 40 consecutive words copied EXACTLY as written in the \
lines, filler words included, and the number is in the quote.
- level is the full price in US dollars as a plain number. People drop \
the thousands: "65" said about Bitcoin is 65000, "1.8" said about \
Ethereum is 1800.

Agreeing with a number somebody else said counts only if he says the \
number himself. Most excerpts contain no levels. Return an empty list \
rather than stretch.\
"""

_SAID = re.compile(r"^\d+ \| [^|]+ \| [^|]+ \| (.*)$")


def has_a_number(window: str) -> bool:
    """True when somebody said a digit. Line numbers and times do not count."""
    for line in window.splitlines():
        match = _SAID.match(line)
        if match and any(ch.isdigit() for ch in match.group(1)):
            return True
    return False


async def read_show(client, model: str, episode: dict, labels: dict[str, str],
                    force: bool) -> list[dict]:
    """The model's proposals for one show, from the cache when it has them."""
    CACHE.mkdir(exist_ok=True)
    kept = CACHE / f"{episode['episode_id']}.json"
    if kept.exists() and not force:
        saved = json.loads(kept.read_text())
        if saved.get("prompt") == PROMPT_VERSION and saved.get("lines") == len(episode["segments"]):
            return saved["raw"]
    parts = [w for w in windows(episode["segments"], labels)
             if has_a_host(w) and has_a_number(w)]
    gate = asyncio.Semaphore(MAX_CONCURRENCY)
    found = await asyncio.gather(*(ask(client, model, w, gate, system=SYSTEM, tool=TOOL)
                                   for w in parts))
    raw = [claim for part in found for claim in part if isinstance(claim, dict)]
    kept.write_text(json.dumps({"prompt": PROMPT_VERSION,
                                "lines": len(episode["segments"]),
                                "windows": len(parts), "raw": raw}, indent=1))
    return raw


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", action="append", default=[],
                    help="a broadcast id, e.g. x-2115904746355851479 (repeatable)")
    ap.add_argument("--all", action="store_true", help="every live show")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    args = ap.parse_args()
    if not args.all and not args.episode:
        ap.error("pass --episode ID or --all")

    settings = get_settings()
    model = os.environ.get("LEVELS_MODEL", settings.search_model)
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    labels = json.loads(LABELS.read_text())
    shows = live_shows(json.loads(EPISODES.read_text()))
    if args.episode:
        shows = [s for s in shows if s["episode_id"] in set(args.episode)]
        if not shows:
            raise SystemExit("no such broadcast in data/episodes.json")

    before = json.loads(OUT.read_text()) if OUT.exists() else {"shows": {}}
    report = dict(before.get("shows", {}))
    for show in shows:
        marks = levels.voices(show, labels)
        if not marks:
            print(f"  no voice labels, skipped: {show['title'][:60]}")
            continue
        raw = await read_show(client, model, show, marks, args.force)
        refused: Counter = Counter()
        passed = []
        for claim in raw:
            found, why = levels.accept(claim, show, marks)
            if found is None:
                refused[why] += 1
            else:
                passed.append(found)
        kept = levels.one_per_show(passed)
        report[show["episode_id"]] = {
            "title": show["title"], "published_at": show.get("published_at"),
            "proposed": len(raw), "refused": dict(refused),
            "candidates": [x.as_row() for x in kept],
        }
        print(f"  {str(show.get('published_at'))[:10]}  {len(raw):3} proposed  "
              f"{len(kept):3} kept  {dict(refused)}  {show['title'][:44]}")
    OUT.write_text(json.dumps({"shows": report}, indent=1, ensure_ascii=False) + "\n")
    total = sum(len(s["candidates"]) for s in report.values())
    print(f"\n{total} candidates across {len(report)} shows -> {OUT.relative_to(ROOT)}")
    if USAGE:
        print(f"model: {USAGE['requests']} requests, {USAGE['input_tokens']:,} in, "
              f"{USAGE['output_tokens']:,} out")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

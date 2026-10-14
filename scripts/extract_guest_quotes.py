"""Find the things worth quoting in what each confirmed guest said.

    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_guest_quotes.py

A model reads each guest's stretch of the show with every line numbered
and marked with whose voice it is in, and proposes quotes. Each proposal
is then held against the transcript and the guest's voice by
app/guests.py, which refuses anything not word for word or not in that voice.

What comes out is data/guest_candidates.json. Nothing here reaches the
site: a person reads the candidates and marks the ones to keep in
data/guest_verdicts.json, and scripts/build_guests.py builds the pages.

Replies are cached per guest in data/.calls_cache/, so a second run costs
nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anthropic import AsyncAnthropic  # noqa: E402

from app import guests  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings, redact  # noqa: E402

DATA = ROOT / "data"
CACHE = DATA / ".calls_cache"
OUT = DATA / "guest_candidates.json"
WINDOW_CHARS = 7000
OVERLAP_LINES = 6
# The proxy is shared with the live site and the bot.
MAX_CONCURRENCY = int(os.environ.get("CALLS_CONCURRENCY", "2"))
PROMPT_VERSION = 1

TOOL = {
    "name": "record_quotes",
    "description": "Record the most quotable things the guest says in this excerpt.",
    "input_schema": {
        "type": "object",
        "properties": {"quotes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "line": {"type": "integer",
                         "description": "The number of the line the quote starts on."},
                "quote": {"type": "string",
                          "description": "12 to 50 consecutive words copied exactly from the guest's lines, filler words included."},
                "about": {"type": "string",
                          "description": "Three to six plain words saying what it is about, e.g. 'why Jito did its airdrop'."},
            },
            "required": ["line", "quote", "about"]}}},
        "required": ["quotes"],
    },
}

SYSTEM = """\
You read an excerpt of Market Bubble, a live show about markets, during an \
interview with one guest, and pick out what the GUEST says that is worth \
quoting.

Each line is written as: number | time | voice | words. The voice is the \
guest's name, "Ansem" or "FaZe Banks" (the hosts), or "?" when it is not \
known. The transcript is automatic: little punctuation, and names are \
sometimes misheard.

Record at most 4 quotes from this excerpt, and often fewer or none. A \
quote is worth recording when a reader who never saw the show would learn \
something from it on its own: a claim, a prediction, a number, how \
something works, why something was done, a story with a point.

Rules:
- Only the guest's words. The quote must START on a line marked with the \
guest's name, and every line it runs across must be the guest's or "?". \
Never a host's line.
- 12 to 50 consecutive words copied EXACTLY as written, filler words \
included. Do not tidy, shorten or join separate sentences.
- It must make sense without the question that prompted it.
- Not small talk, not thanks, not a greeting, not a sponsor read, not half \
of a sentence.\
"""


def windows(segments: list[dict], first: int, last: int, voice: set[int],
            hosts: dict[str, str], name: str) -> list[str]:
    lines = []
    for index in range(first, last + 1):
        seg = segments[index]
        t = int(seg.get("t", 0))
        stamp = f"{t // 3600}:{t % 3600 // 60:02}:{t % 60:02}" if t >= 3600 else f"{t // 60}:{t % 60:02}"
        who = name if index in voice else (hosts.get(str(index)) or "?")
        lines.append(f"{index} | {stamp} | {who} | {' '.join(str(seg.get('text', '')).split())}")
    out, at = [], 0
    while at < len(lines):
        size, end = 0, at
        while end < len(lines) and (end == at or size + len(lines[end]) <= WINDOW_CHARS):
            size += len(lines[end])
            end += 1
        out.append("\n".join(lines[at:end]))
        if end >= len(lines):
            break
        at = max(end - OVERLAP_LINES, at + 1)
    return out


async def ask(client, model: str, window: str, gate: asyncio.Semaphore) -> list[dict]:
    async with gate:
        for attempt in range(4):
            try:
                reply = await client.messages.create(
                    model=model, max_tokens=2048,
                    system=[{"type": "text", "text": SYSTEM,
                             "cache_control": {"type": "ephemeral"}}],
                    tools=[TOOL], tool_choice={"type": "tool", "name": "record_quotes"},
                    messages=[{"role": "user", "content": f"<excerpt>\n{window}\n</excerpt>"}])
                for block in reply.content:
                    if block.type == "tool_use":
                        found = block.input.get("quotes", [])
                        return found if isinstance(found, list) else []
                return []
            except Exception as exc:                            # noqa: BLE001
                if attempt == 3:
                    raise RuntimeError(
                        f"a window failed four times: {redact(str(exc))[:160]}") from exc
                await asyncio.sleep(3 * (attempt + 1))
    return []


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[], metavar="SLUG")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    model = os.environ.get("CALLS_MODEL", settings.search_model)
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    episodes: dict[str, dict] = {}
    for episode in json.loads((DATA / "episodes.json").read_text()):
        episodes.setdefault(episode["episode_id"], episode)
    roster = {g["slug"]: g for g in json.loads((DATA / "guest_roster.json").read_text())["guests"]}
    voices = json.loads((DATA / "guest_voices.json").read_text())
    hosts_all = json.loads((DATA / "speaker_map.json").read_text())
    report = json.loads(OUT.read_text()) if OUT.exists() else {}
    CACHE.mkdir(exist_ok=True)

    for slug, guest in roster.items():
        if args.only and slug not in args.only:
            continue
        if slug not in voices:
            print(f"  {slug}: no voice on file, run scripts/guest_voices.py")
            continue
        episode = episodes[guest["episode_id"]]
        voice = set(voices[slug]["lines"])
        hosts = hosts_all.get(guest["episode_id"], {})
        kept = CACHE / f"guest-{slug}.json"
        raw = None
        if kept.exists() and not args.force:
            saved = json.loads(kept.read_text())
            if saved.get("prompt") == PROMPT_VERSION and saved.get("lines") == len(voice):
                raw = saved["raw"]
        if raw is None:
            parts = windows(episode["segments"], min(voice), max(voice), voice, hosts, guest["name"])
            gate = asyncio.Semaphore(MAX_CONCURRENCY)
            found = await asyncio.gather(*(ask(client, model, w, gate) for w in parts))
            raw = [q for part in found for q in part if isinstance(q, dict)]
            kept.write_text(json.dumps({"prompt": PROMPT_VERSION, "lines": len(voice),
                                        "raw": raw}, indent=1))
        refused: Counter = Counter()
        passed = []
        for claim in raw:
            quote, why = guests.accept(claim, episode, voice, hosts)
            if quote is None:
                refused[why] += 1
            else:
                passed.append(quote)
        candidates = guests.apart(passed)
        report[slug] = {"episode_id": guest["episode_id"], "proposed": len(raw),
                        "refused": dict(refused), "candidates": candidates}
        print(f"  {slug:18} {len(raw):3} proposed  {len(candidates):3} kept  {dict(refused)}")
    OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(f"\n{sum(len(g['candidates']) for g in report.values())} candidates -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

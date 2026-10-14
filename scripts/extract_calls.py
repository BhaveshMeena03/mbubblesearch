"""Find the calls the hosts made on air.

    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_calls.py --episode x-2108360500283335093
    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_calls.py --all

A model reads each live show a few minutes at a time, with every line
numbered and marked with whose voice it is in, and proposes calls: a host
saying he holds something, naming his pick, putting a price on it, or
selling it. Each proposal is then held against the transcript by
app/calls.py, which refuses anything whose quote is not there word for
word or whose line is not in that host's voice.

What comes out is data/calls_candidates.json: proposals that survived,
and a count of the ones that did not and why. Nothing here reaches the
site. A person reads the candidates and records a verdict on each in
data/calls_verdicts.json, and scripts/build_calls.py builds the board
from the ones marked keep.

Only the live broadcasts are read. The YouTube uploads are cuts of the
same shows, so reading both would find every call twice, and only the
broadcast plays at the second on X.

    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/extract_calls.py --posts

reads what Ansem posted instead: his own posts and the comments he wrote
above posts he quoted, from data/ansem_posts.json. Replies are left out,
because a reply is half an exchange and the other half is not stored. No
voice check is needed there, since a post on his account is his, and the
quote is held against the post. The posts a candidate came from are
written to data/calls_posts.json, which ships, so a test can hold every
quoted post against the row that quotes it.

The model's raw replies are cached per show in data/.calls_cache/, so a
run that dies picks up where it stopped and a second run costs nothing.
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

from app import calls  # noqa: E402
from app import posts as posts_page  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings, redact  # noqa: E402

EPISODES = ROOT / "data" / "episodes.json"
LABELS = ROOT / "data" / "speaker_map.json"
CACHE = ROOT / "data" / ".calls_cache"
OUT = ROOT / "data" / "calls_candidates.json"
POSTS = ROOT / "data" / "ansem_posts.json"
QUOTED = ROOT / "data" / "calls_posts.json"
# Bump when POSTS_SYSTEM changes and every post should be read again.
POSTS_PROMPT_VERSION = 1

# Lines per request, and how many are repeated at the start of the next
# one so a call that straddles the cut is whole in one of them.
WINDOW_CHARS = int(os.environ.get("CALLS_WINDOW_CHARS", "7000"))
OVERLAP_LINES = 6
# The proxy is shared with the live site and the bot. Two, because four
# drew "429 Too Many Requests" on the first full run and a site question
# asked at the same moment waited a minute for its answer.
MAX_CONCURRENCY = int(os.environ.get("CALLS_CONCURRENCY", "2"))
# Bump when the instructions change and every show should be read again: a
# cached reply is only as good as the question it answered. Not bumped for
# the 12 October 2026 line about hedges and trades, because every candidate
# from before it had already been read by a person with that rule in hand,
# and reading 23 shows again costs what it cost the first time.
PROMPT_VERSION = 1

TOOL = {
    "name": "record_calls",
    "description": "Record every call a host makes in this excerpt.",
    "input_schema": {
        "type": "object",
        "properties": {
            "calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "integer",
                                 "description": "The number of the line the quote starts on."},
                        "speaker": {"type": "string", "enum": list(calls.HOSTS),
                                    "description": "The voice marked on that line."},
                        "asset": {"type": "string",
                                  "description": "The asset's name, e.g. Starknet, Pump.fun, Nvidia."},
                        "symbol": {"type": "string",
                                   "description": "Its ticker in caps, e.g. STRK, PUMP, NVDA. Empty if unsure."},
                        "asset_class": {"type": "string",
                                        "enum": ["crypto", "stock", "index", "commodity", "other"]},
                        "stance": {"type": "string", "enum": list(calls.STANCES),
                                   "description": "long if he owns it, is buying it or expects it up; short if he is short it or expects it down. For an exit, the side he was on."},
                        "kind": {"type": "string", "enum": list(calls.KINDS)},
                        "quote": {"type": "string",
                                  "description": "8 to 40 consecutive words copied exactly from the lines, including filler words, that contain the call."},
                        "target": {"type": "string",
                                   "description": "For kind=target, the level in his words, e.g. '100 billion market cap'. Otherwise empty."},
                    },
                    "required": ["line", "speaker", "asset", "symbol", "asset_class",
                                 "stance", "kind", "quote", "target"],
                },
            }
        },
        "required": ["calls"],
    },
}

SYSTEM = """\
You read excerpts of Market Bubble, a live show about markets hosted by \
Ansem and FaZe Banks, and record the CALLS the two hosts make.

Each line is written as: number | time | voice | words. The voice is \
"Ansem", "FaZe Banks" or "?" when it is not known (guests and crosstalk \
are "?"). The transcript is automatic: no punctuation, and names are often \
misheard ("z cash", "soul", "hype" for Hyperliquid).

A call is a host putting HIMSELF on record about ONE specific tradeable \
asset. There are four kinds:
- holding: he says he owns it, is long or short it, bought it or is buying it.
- pick: he names it as what he would buy, his pick, his highest conviction.
- target: he predicts a specific price or market cap for it.
- exit: he says he sold it or closed the position.

Record a call only when ALL of this is true:
- The line the quote starts on is marked Ansem or FaZe Banks. Never record \
a call from a "?" line, however sure you are who is talking.
- He is speaking for himself, now. Not describing what someone else owns \
or thinks, not a question, not a joke, not a hypothetical, not a story \
about a trade from years ago.
- It is a view he would stand behind, not a trade. Not a hedge ("I'm \
hedged short the majors against my spot"), not a position taken off the \
chart for a few days, not a setup with an entry, a stop and a target, not \
an idea for a 30 day challenge.
- It is one named asset: a token, a stock, a commodity. Not a category \
("alts", "AI stocks", "memes"), not a prediction-market bet, not a sponsor.
- The quote is 8 to 40 consecutive words copied EXACTLY as written in the \
lines, filler words included, and the asset is clear from the quote or the \
line just before it.

Most excerpts contain no calls. Return an empty list rather than stretch. \
Describing an asset, praising a team, or saying a chart looks good is not a \
call unless he says he owns it, would buy it, or puts a number on it.\
"""


POSTS_TOOL = {
    "name": "record_calls",
    "description": "Record every call he makes in these posts.",
    "input_schema": {
        "type": "object",
        "properties": {
            "calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "post": {"type": "string",
                                 "description": "The id of the post the call is in."},
                        "asset": TOOL["input_schema"]["properties"]["calls"]["items"]["properties"]["asset"],
                        "symbol": TOOL["input_schema"]["properties"]["calls"]["items"]["properties"]["symbol"],
                        "asset_class": TOOL["input_schema"]["properties"]["calls"]["items"]["properties"]["asset_class"],
                        "stance": TOOL["input_schema"]["properties"]["calls"]["items"]["properties"]["stance"],
                        "kind": {"type": "string", "enum": list(calls.KINDS)},
                        "quote": {"type": "string",
                                  "description": "6 to 40 consecutive words copied exactly from that post that contain the call."},
                        "target": TOOL["input_schema"]["properties"]["calls"]["items"]["properties"]["target"],
                    },
                    "required": ["post", "asset", "symbol", "asset_class",
                                 "stance", "kind", "quote", "target"],
                },
            }
        },
        "required": ["calls"],
    },
}

POSTS_SYSTEM = """\
You read posts written on X by Ansem (@blknoiz06), a trader and co-host of \
the Market Bubble show, and record the CALLS he makes in them.

Each post is given with its id, its date and its kind: "post" is his own \
post; "quote" is the comment he wrote above somebody else's post, and you \
are shown only his comment. Every word you see is his.

A call is him putting HIMSELF on record about ONE specific tradeable \
asset. There are four kinds:
- holding: he says he owns it, is long or short it, bought it or is buying it.
- pick: he names it as what he would buy, his pick, his highest conviction.
- target: he predicts a specific price or market cap for it.
- exit: he says he sold it or closed the position.

Record a call only when ALL of this is true:
- He is speaking for himself, now. Not reporting what happened to a price, \
not a question, not a joke, not a hypothetical, not a story about an old \
trade, not a giveaway, not a list of what an episode covered.
- It is a view he would stand behind, not a trade. Not a hedge, not a \
position taken off the chart for a few days, not a setup with an entry, a \
stop and a target.
- It is one named asset: a token, a stock, a commodity. Not a category, \
not a prediction-market bet, not a sponsor.
- The quote is 6 to 40 consecutive words copied EXACTLY from that one \
post, and the asset is named in the quote.

Most posts contain no calls. Return an empty list rather than stretch. \
Praising a team or saying a chart looks good is not a call unless he says \
he owns it, would buy it, or puts a number on it.\
"""


def posts_worth_reading() -> list[dict]:
    """His own posts and his comments on quoted posts, as he typed them."""
    found = []
    for post in json.loads(POSTS.read_text()):
        kind = posts_page.kind_of(post)
        text = posts_page.readable(post.get("text"))
        if kind == "reply" or text.startswith("RT @"):
            continue
        if posts_page.day_number(post.get("created_at")) is None:
            continue
        # Too short to hold an asset, a stance and six words of quote.
        if len(calls.words(text)) < calls.MIN_WORDS + 2:
            continue
        found.append({"id": str(post["id"]), "created_at": post["created_at"],
                      "kind": kind, "text": text})
    return sorted(found, key=lambda p: (p["created_at"], p["id"]))


def post_windows(found: list[dict]) -> list[str]:
    out, current, size = [], [], 0
    for post in found:
        block = (f'<post id="{post["id"]}" on="{post["created_at"][:10]}" '
                 f'kind="{post["kind"]}">\n{post["text"]}\n</post>')
        if current and size + len(block) > WINDOW_CHARS:
            out.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block)
    if current:
        out.append("\n\n".join(current))
    return out


async def read_posts(client, model: str, force: bool) -> dict:
    """Candidates from his posts, and the posts they came from."""
    found = posts_worth_reading()
    by_id = {p["id"]: p for p in found}
    CACHE.mkdir(exist_ok=True)
    kept = CACHE / "posts.json"
    raw = None
    if kept.exists() and not force:
        saved = json.loads(kept.read_text())
        if saved.get("prompt") == POSTS_PROMPT_VERSION and saved.get("posts") == len(found):
            raw = saved["raw"]
    if raw is None:
        gate = asyncio.Semaphore(MAX_CONCURRENCY)
        parts = await asyncio.gather(*(
            ask(client, model, w, gate, system=POSTS_SYSTEM, tool=POSTS_TOOL)
            for w in post_windows(found)))
        raw = [claim for part in parts for claim in part if isinstance(claim, dict)]
        kept.write_text(json.dumps({"prompt": POSTS_PROMPT_VERSION,
                                    "posts": len(found), "raw": raw}, indent=1))
    refused: Counter = Counter()
    passed = []
    for claim in raw:
        post = by_id.get(str(claim.get("post") or ""))
        if post is None:
            refused["no such post"] += 1
            continue
        row, why = calls.accept_post(claim, post, posts_page.AUTHOR)
        if row is None:
            refused[why] += 1
        else:
            passed.append(row)
    rows = calls.one_per_day(passed)
    quoted = {r["post_id"]: by_id[r["post_id"]] for r in rows}
    QUOTED.write_text(json.dumps(dict(sorted(quoted.items())), indent=1,
                                 ensure_ascii=False) + "\n")
    print(f"  posts: {len(found)} read  {len(raw)} proposed  {len(rows)} kept  {dict(refused)}")
    return {"read": len(found), "proposed": len(raw), "refused": dict(refused),
            "candidates": rows}


def windows(segments: list[dict], labels: dict[str, str]) -> list[str]:
    """The show as numbered, voice-marked lines, a few thousand characters
    at a time."""
    lines = []
    for index, seg in enumerate(segments):
        t = int(seg.get("t", 0))
        stamp = f"{t // 3600}:{t % 3600 // 60:02}:{t % 60:02}" if t >= 3600 else f"{t // 60}:{t % 60:02}"
        who = labels.get(str(index)) or "?"
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


def has_a_host(window: str) -> bool:
    """A window with no line in a host's voice cannot hold a call."""
    return any(f"| {host} |" in window for host in calls.HOSTS)


USAGE = Counter()


async def ask(client: AsyncAnthropic, model: str, window: str,
              gate: asyncio.Semaphore, *, system: str | None = None,
              tool: dict | None = None) -> list[dict]:
    async with gate:
        for attempt in range(4):
            try:
                reply = await client.messages.create(
                    model=model, max_tokens=2048,
                    system=[{"type": "text", "text": system or SYSTEM,
                             "cache_control": {"type": "ephemeral"}}],
                    tools=[tool or TOOL],
                    tool_choice={"type": "tool", "name": "record_calls"},
                    messages=[{"role": "user",
                               "content": f"<excerpt>\n{window}\n</excerpt>"}],
                )
                use = getattr(reply, "usage", None)
                USAGE["requests"] += 1
                USAGE["input_tokens"] += getattr(use, "input_tokens", 0) or 0
                USAGE["output_tokens"] += getattr(use, "output_tokens", 0) or 0
                for block in reply.content:
                    if block.type == "tool_use":
                        found = block.input.get("calls", [])
                        return found if isinstance(found, list) else []
                return []
            except Exception as exc:                            # noqa: BLE001
                if attempt == 3:
                    # Raised, not returned as []: an empty list would be
                    # cached as "this stretch holds no calls", for good.
                    raise RuntimeError(
                        f"a window failed four times: {redact(str(exc))[:160]}") from exc
                await asyncio.sleep(3 * (attempt + 1))
    return []


async def read_show(client, model: str, episode: dict, labels: dict[str, str],
                    force: bool) -> list[dict]:
    """The model's proposals for one show, from the cache when it has them."""
    CACHE.mkdir(exist_ok=True)
    kept = CACHE / f"{episode['episode_id']}.json"
    if kept.exists() and not force:
        saved = json.loads(kept.read_text())
        if saved.get("prompt") == PROMPT_VERSION and saved.get("lines") == len(episode["segments"]):
            return saved["raw"]
    parts = [w for w in windows(episode["segments"], labels) if has_a_host(w)]
    gate = asyncio.Semaphore(MAX_CONCURRENCY)
    found = await asyncio.gather(*(ask(client, model, w, gate) for w in parts))
    raw = [claim for part in found for claim in part if isinstance(claim, dict)]
    kept.write_text(json.dumps({"prompt": PROMPT_VERSION,
                                "lines": len(episode["segments"]),
                                "windows": len(parts), "raw": raw}, indent=1))
    return raw


def live_shows(episodes: list[dict]) -> list[dict]:
    """One row per broadcast, oldest first. episodes.json can hold a show
    twice; the first copy is the one the index was built from."""
    seen: dict[str, dict] = {}
    for episode in episodes:
        ident = str(episode.get("episode_id") or "")
        if ident.startswith("x-") and ident not in seen:
            seen[ident] = episode
    return sorted(seen.values(), key=lambda e: str(e.get("published_at") or ""))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", action="append", default=[],
                    help="a broadcast id, e.g. x-2108360500283335093 (repeatable)")
    ap.add_argument("--all", action="store_true", help="every live show")
    ap.add_argument("--posts", action="store_true",
                    help="his posts on X instead of the shows")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    args = ap.parse_args()
    if not args.all and not args.episode and not args.posts:
        ap.error("pass --episode ID, --all, or --posts")

    settings = get_settings()
    model = os.environ.get("CALLS_MODEL", settings.search_model)
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    labels = json.loads(LABELS.read_text())
    shows = live_shows(json.loads(EPISODES.read_text()))
    if args.episode:
        shows = [s for s in shows if s["episode_id"] in set(args.episode)]
        if not shows:
            raise SystemExit("no such broadcast in data/episodes.json")

    before = json.loads(OUT.read_text()) if OUT.exists() else {"shows": {}}
    report = dict(before.get("shows", {}))
    from_posts = before.get("posts")
    if args.posts:
        from_posts = await read_posts(client, model, args.force)
        shows = []
    for show in shows:
        marks = labels.get(show["episode_id"], {})
        if not marks:
            print(f"  no voice labels, skipped: {show['title'][:60]}")
            continue
        raw = await read_show(client, model, show, marks, args.force)
        refused: Counter = Counter()
        passed = []
        for claim in raw:
            call, why = calls.accept(claim, show, marks)
            if call is None:
                refused[why] += 1
            else:
                passed.append(call)
        kept = calls.one_per_show(passed)
        report[show["episode_id"]] = {
            "title": show["title"], "published_at": show.get("published_at"),
            "proposed": len(raw), "refused": dict(refused),
            "candidates": [c.as_row() for c in kept],
        }
        print(f"  {str(show.get('published_at'))[:10]}  {len(raw):3} proposed  "
              f"{len(kept):3} kept  {dict(refused)}  {show['title'][:44]}")
    out = {"shows": report}
    if from_posts:
        out["posts"] = from_posts
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    total = sum(len(s["candidates"]) for s in report.values())
    print(f"\n{total} candidates across {len(report)} shows"
          + (f", {len(from_posts['candidates'])} from posts" if from_posts else "")
          + f" -> {OUT.relative_to(ROOT)}")
    if USAGE:
        print(f"model: {USAGE['requests']} requests, {USAGE['input_tokens']:,} in, "
              f"{USAGE['output_tokens']:,} out")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

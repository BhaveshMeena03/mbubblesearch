"""Extract every asset (crypto token, stock, index) discussed across the
Market Bubble episodes, with the timestamp where the discussion starts.

The point is not a mention counter. The hosts do real analysis — charts,
theses, price context — so each mention is classified as substantive
`analysis` or a passing `mention`, and carries a short factual note about
what was actually said. That's what makes the output worth browsing.

Auto-generated captions garble ticker names ("BONK" -> "bunk", "WIF" ->
"with"), so extraction is done by a model with surrounding context rather
than a regex, and every hit carries a confidence the caller can filter on.
Results are cached per episode, so re-running is free and resumable.

    python scripts/extract_assets.py --episodes 2      # cheap pilot
    python scripts/extract_assets.py --all             # full pass
    python scripts/extract_assets.py --all --force     # ignore cache
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from anthropic import AsyncAnthropic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets import (  # noqa: E402,F401
    aggregate,
    canonical,
    deep_link,
    is_asset,
    timestamp,
)
from app.assets_store import AssetStore  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("extract")

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "data" / "episodes.json"
OUT = ROOT / "data" / "assets.json"
CACHE = ROOT / "data" / ".asset_cache"

# Window size in characters. Bigger windows = fewer calls = cheaper, but the
# model needs enough context to tell analysis from a name-drop.
WINDOW_CHARS = int(os.environ.get("EXTRACT_WINDOW_CHARS", "6000"))
# Four was sized for a fifteen-episode archive. MCG is 645, each about
# thirty windows, and at four in flight the whole pass measured ~58s an
# episode -- ten hours of wall clock for work that costs the same in
# tokens whatever the rate. Configurable rather than raised outright,
# because the right number belongs to the run and not to the file.
MAX_CONCURRENCY = int(os.environ.get("EXTRACT_CONCURRENCY", "4"))

EXTRACT_TOOL = {
    "name": "record_assets",
    "description": "Record every financial asset discussed in this excerpt.",
    "input_schema": {
        "type": "object",
        "properties": {
            "assets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Ticker in caps, e.g. SOL, BTC, AAPL. "
                                           "No $ prefix.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Full name, e.g. Solana, Apple.",
                        },
                        "asset_class": {
                            "type": "string",
                            "enum": ["crypto", "stock", "index", "commodity", "other"],
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["analysis", "mention"],
                            "description": "'analysis' = they actually discuss it "
                                           "(thesis, chart, price action, outlook). "
                                           "'mention' = named only in passing.",
                        },
                        "start_seconds": {
                            "type": "number",
                            "description": "Timestamp where this discussion starts, "
                                           "from the [t=...] markers.",
                        },
                        "note": {
                            "type": "string",
                            "description": "One factual sentence on what was said "
                                           "about it. Describe only; do not add "
                                           "interpretation or advice.",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "How sure you are this asset was really "
                                           "named (captions are auto-generated and "
                                           "often garble tickers).",
                        },
                    },
                    "required": ["symbol", "name", "asset_class", "kind",
                                 "start_seconds", "note", "confidence"],
                },
            }
        },
        "required": ["assets"],
    },
}

SYSTEM = """\
You extract financial assets discussed in podcast transcript excerpts.

The transcript is an AUTO-GENERATED caption track. It contains transcription \
errors, and crypto ticker names are frequently garbled (BONK->"bunk", \
WIF->"with", SOL->"soul"). Use surrounding context to judge what was really \
said, and mark `confidence` honestly — "low" when you're inferring from a \
garbled word, "high" only when it's unambiguous.

Rules:
- Only record assets that are actually discussed or named. Never invent one.
- If the excerpt discusses no assets, return an empty list.
- `kind`: use "analysis" only when they genuinely engage with it — a thesis, \
chart talk, price action, an outlook. Use "mention" for a passing name-drop.
- `start_seconds` must come from the [t=...] markers in the excerpt.
- `note` describes ONLY what the speakers said, factually. Do not add your own \
view, and do not phrase it as a recommendation.
- Do not record generic words ("the market", "stocks") as assets.\
"""


def _windows(segments: list[dict], budget: int) -> list[tuple[float, str]]:
    """Group segments into character-bounded windows, keeping timestamps."""
    out: list[tuple[float, str]] = []
    cur: list[str] = []
    start = 0.0
    size = 0
    for seg in segments:
        line = f"[t={seg['t']:.0f}] {seg['text']}"
        if cur and size + len(line) > budget:
            out.append((start, "\n".join(cur)))
            cur, size = [], 0
        if not cur:
            start = float(seg["t"])
        cur.append(line)
        size += len(line)
    if cur:
        out.append((start, "\n".join(cur)))
    return out


# What a run actually spent, filled in as the responses come back. Printed
# at the end of a run and by the MCG extractor, because "about two dollars"
# was an estimate from a character count and the only way to know what a
# second archive costs before committing to 645 episodes of it is to meter
# the first few.
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
         "cache_writes": 0, "cache_reads": 0}


def _meter(resp) -> None:
    use = getattr(resp, "usage", None)
    if use is None:
        return
    USAGE["calls"] += 1
    USAGE["input_tokens"] += getattr(use, "input_tokens", 0) or 0
    USAGE["output_tokens"] += getattr(use, "output_tokens", 0) or 0
    # Cached input is billed at a tenth of the rate, so a run that reads the
    # cache and a run that does not are different prices for identical work.
    # Counted separately rather than folded in, because a proxy that
    # silently drops cache_control would otherwise look like a cheap run.
    USAGE["cache_writes"] += getattr(use, "cache_creation_input_tokens", 0) or 0
    USAGE["cache_reads"] += getattr(use, "cache_read_input_tokens", 0) or 0


async def _extract_window(
    client: AsyncAnthropic, model: str, text: str, sem: asyncio.Semaphore
) -> list[dict]:
    async with sem:
        for attempt in range(4):
            try:
                resp = await client.messages.create(
                    model=model,
                    max_tokens=2048,
                    # The instructions and the schema are byte-identical on
                    # every call and were being re-sent with each one: at
                    # 6,000-character windows they were about half the input
                    # tokens of the entire run. Marked cacheable, they are
                    # billed once per five-minute window and read back at a
                    # tenth of the rate. Nothing about the output changes.
                    system=[{"type": "text", "text": SYSTEM,
                             "cache_control": {"type": "ephemeral"}}],
                    tools=[EXTRACT_TOOL],
                    tool_choice={"type": "tool", "name": "record_assets"},
                    messages=[{"role": "user", "content": f"<excerpt>\n{text}\n</excerpt>"}],
                )
                _meter(resp)
                for block in resp.content:
                    if block.type == "tool_use":
                        return block.input.get("assets", [])
                return []
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    # Raise rather than return []. An empty list here is
                    # indistinguishable from "this window discussed no
                    # assets", and extract_episode writes that straight to
                    # the cache -- so a run that lost its credentials would
                    # record every episode as silent, permanently, and the
                    # next run would trust the cache and skip them.
                    raise RuntimeError(
                        f"window failed after 4 attempts: {exc}") from exc
                await asyncio.sleep(2 * (attempt + 1))
        raise RuntimeError("window exhausted its retries")


async def extract_episode(
    client: AsyncAnthropic, model: str, ep: dict, force: bool
) -> list[dict]:
    CACHE.mkdir(exist_ok=True)
    cache_file = CACHE / f"{ep['episode_id']}.json"
    if cache_file.exists() and not force:
        logger.info("  cached: %s", ep["title"][:56])
        return json.loads(cache_file.read_text())

    wins = _windows(ep["segments"], WINDOW_CHARS)
    logger.info("  %-56s %d windows", ep["title"][:56], len(wins))
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(
        *[_extract_window(client, model, text, sem) for _, text in wins]
    )

    hits: list[dict] = []
    for found in results:
        for a in found:
            try:
                hits.append({
                    "symbol": a["symbol"].strip().upper().lstrip("$"),
                    "name": a.get("name", "").strip(),
                    "asset_class": a.get("asset_class", "other"),
                    "kind": a.get("kind", "mention"),
                    "start_seconds": float(a.get("start_seconds", 0)),
                    "note": a.get("note", "").strip(),
                    "confidence": a.get("confidence", "low"),
                    "episode_id": ep["episode_id"],
                    "episode_title": ep["title"],
                    "url": ep.get("url", ""),
                })
            except (KeyError, TypeError, ValueError):
                continue
    cache_file.write_text(json.dumps(hits, indent=2))
    return hits


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=0,
                    help="only process the first N episodes (pilot)")
    ap.add_argument("--all", action="store_true", help="process every episode")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="permit a report that covers fewer episodes than "
                         "the one already on disk")
    ap.add_argument("--min-confidence", default="medium",
                    choices=["low", "medium", "high"])
    ap.add_argument("--store", action="store_true",
                    help="also upsert per-episode hits to Pinecone, so the "
                         "deployed dashboard picks them up without a redeploy")
    args = ap.parse_args()

    if not args.all and not args.episodes:
        ap.error("pass --episodes N for a pilot, or --all")

    settings = get_settings()
    model = os.environ.get("EXTRACT_MODEL", "claude-haiku-4-5")
    # Through the proxy, like everything else that spends money on a
    # model. Built bare, this went straight to api.anthropic.com on a
    # key that only the proxy accepts, and every window 401'd.
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))

    episodes = json.loads(EPISODES.read_text())
    if not args.all:
        episodes = episodes[: args.episodes]

    logger.info("Extracting assets from %d episode(s) with %s\n",
                len(episodes), model)
    store = AssetStore() if args.store else None
    all_hits: list[dict] = []
    for ep in episodes:
        hits = await extract_episode(client, model, ep, args.force)
        all_hits.extend(hits)
        if store is not None:
            # Per-episode write: a failure here must not lose the extraction
            # work already cached on disk.
            try:
                n = await store.store(ep["episode_id"], ep["title"], hits)
                logger.info("  stored %d hits for %s", n, ep["episode_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("  store FAILED for %s (%s) — cached locally, "
                               "re-run with --store to retry",
                               ep["episode_id"], exc)

    report = aggregate(all_hits, args.min_confidence)
    report["episodes_processed"] = len(episodes)

    # The report is rebuilt from scratch each run, so a partial run does not
    # add to the index -- it replaces it. One `--episodes 15` run cut a
    # 21-show index down to 10, and the loss only surfaced when a token the
    # hosts had discussed on air could not be found on the dashboard.
    def _covered(rep: dict) -> set[str]:
        return {m["episode_id"] for a in rep.get("assets", [])
                for m in a.get("moments", [])}

    if OUT.exists() and not args.allow_shrink:
        lost = _covered(json.loads(OUT.read_text())) - _covered(report)
        if lost:
            raise SystemExit(
                f"refusing to write {OUT.name}: it would drop {len(lost)} "
                f"episode(s) already indexed, e.g. {sorted(lost)[:3]}. "
                "Re-run with --all, or pass --allow-shrink to overwrite.")
    OUT.write_text(json.dumps(report, indent=2))

    logger.info("\n%-8s %-22s %8s %9s %8s", "SYMBOL", "NAME", "ANALYSIS",
                "MENTIONS", "EPS")
    for a in report["assets"][:25]:
        logger.info("%-8s %-22s %8d %9d %8d", a["symbol"], a["name"][:22],
                    a["analysis"], a["mentions"], a["episode_count"])
    logger.info("\n%d assets | %d hits kept | %d dropped as low-confidence",
                len(report["assets"]), report["total_hits"],
                report["dropped_low_confidence"])
    logger.info("-> %s", OUT.relative_to(ROOT))


if __name__ == "__main__":
    asyncio.run(main())

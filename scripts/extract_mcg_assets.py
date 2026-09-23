"""Extract every asset discussed on MCG Live, from the index rather than
from transcripts.

    .venv/bin/python scripts/extract_mcg_assets.py --episodes 2
    .venv/bin/python scripts/extract_mcg_assets.py --all --store

Same extractor, same prompt, same model as the Market Bubble pass -- the
difference is where the words come from. That archive keeps its transcripts
in data/episodes.json; this one deliberately does not (see ingest_mcg.py),
so the only copy of the text is the metadata on the vectors themselves.

That turns out to be enough. The 21,527 vectors tile 1,024.7 hours rather
than overlapping -- roughly one window every three minutes, about 2,500
characters each -- so rebuilding an episode is a filtered query, a parse
and a sort. Nothing is re-downloaded and nothing is re-transcribed, which
is the difference between a few dollars and a week of laptop time.

The timestamps live in two shapes and both have to be read. Interviews
carry `text_ts`, the stamped copy; streams carry `text` with a
comma-separated `line_times` beside it, which is a hundred bytes against a
duplicate of the whole passage. app/podcast.py._stamped knows both, so it
is imported rather than reimplemented here -- reading only `text_ts`, as
this did at first, skipped 230 episodes and 727 hours without once
failing, because every one of them simply looked empty.

Rows land in the MCG index, in a namespace of its own. Two archives writing
assets into one namespace would produce rows where NVDA carries moments
from two different shows with no way to tell them apart, which is exactly
the mistake that put thirteen MCG passages in the concierge's index.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assets import aggregate  # noqa: E402
from app.assets_store import AssetStore  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings  # noqa: E402
from app.mcg_transcript import (  # noqa: E402,F401
    MAX_WINDOWS,
    rebuild,
    seconds,
    to_segments,
)
from scripts.extract_assets import USAGE, extract_episode  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mcg-assets")

SHELF = ROOT / "data" / "mcg_index.json"
OUT = ROOT / "data" / "mcg_assets.json"

# The namespace assets live in inside the MCG index. Its passages are in
# "mcg"; these are beside them, not among them.
ASSET_NAMESPACE = "assets"

async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=0,
                    help="only the first N, newest first (pilot)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.add_argument("--store", action="store_true",
                    help="upsert to Pinecone, so the deployed page sees it")
    ap.add_argument("--min-confidence", default="medium",
                    choices=["low", "medium", "high"])
    args = ap.parse_args()
    if not args.all and not args.episodes:
        ap.error("pass --episodes N for a pilot, or --all")

    settings = get_settings()
    shelf = json.loads(SHELF.read_text())
    todo = shelf if args.all else shelf[:args.episodes]

    from pinecone import Pinecone
    index = Pinecone(api_key=settings.pinecone_api_key).Index(
        settings.mcg_pinecone_index)

    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    model = os.environ.get("EXTRACT_MODEL", "claude-haiku-4-5")
    store = AssetStore(index_name=settings.mcg_pinecone_index,
                       namespace=ASSET_NAMESPACE) if args.store else None

    logger.info("%d episodes, model %s", len(todo), model)
    every: list[dict] = []
    for n, row in enumerate(todo, 1):
        segments = rebuild(index, settings.mcg_namespace,
                           settings.embedding_dimension, row["id"])
        if not segments:
            logger.warning("  %s: no passages in the index — skipped", row["id"])
            continue
        episode = {"episode_id": row["id"], "title": row["title"],
                   "url": row["url"], "segments": segments}
        logger.info("[%d/%d] %s", n, len(todo), row["title"][:56])
        hits = await extract_episode(client, model, episode, args.force)
        every.extend(hits)
        if store is not None and hits:
            await store.store(row["id"], row["title"], hits)

    report = aggregate(every, min_confidence=args.min_confidence)
    OUT.write_text(json.dumps(report, indent=1))
    logger.info("\n  %d assets from %d hits -> %s",
                len(report.get("assets", [])), report.get("total_hits", 0),
                OUT.relative_to(ROOT))

    if USAGE["calls"]:
        logger.info("  spent: %d calls, %s input tokens, %s output tokens",
                    USAGE["calls"], f"{USAGE['input_tokens']:,}",
                    f"{USAGE['output_tokens']:,}")
        logger.info("  per episode: %.0f calls, %.0f input tokens",
                    USAGE["calls"] / max(1, len(todo)),
                    USAGE["input_tokens"] / max(1, len(todo)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

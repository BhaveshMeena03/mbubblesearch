"""Extract every asset discussed on MCG Live, from the index rather than
from transcripts.

    .venv/bin/python scripts/extract_mcg_assets.py --episodes 2
    .venv/bin/python scripts/extract_mcg_assets.py --all --store
    .venv/bin/python scripts/extract_mcg_assets.py --archive threadguy --all --store

ThreadGuy is the same kind of archive, with no transcripts on disk either,
so it runs through here with its own shelf, index and namespaces rather
than a copy of this file.

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

# Where each archive's passages are read from and its assets written to.
# The assets go in a namespace of their own beside the passages, never
# among them: MCG's in "assets" inside the MCG index, ThreadGuy's in
# "threadguy_assets" inside the default one, because "assets" there is
# already Market Bubble's.
ARCHIVES = {
    "mcg": {
        "shelf": ROOT / "data" / "mcg_index.json",
        "out": ROOT / "data" / "mcg_assets.json",
        "index": lambda s: s.mcg_pinecone_index,
        "passages": lambda s: s.mcg_namespace,
        "assets": "assets",
    },
    "threadguy": {
        "shelf": ROOT / "data" / "threadguy_index.json",
        "out": ROOT / "data" / "threadguy_assets.json",
        "index": lambda s: s.pinecone_index,
        "passages": lambda s: s.threadguy_namespace,
        "assets": "threadguy_assets",
    },
}

async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="mcg", choices=sorted(ARCHIVES))
    ap.add_argument("--episodes", type=int, default=0,
                    help="only the first N, newest first (pilot)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.add_argument("--store", action="store_true",
                    help="upsert to Pinecone, so the deployed page sees it")
    ap.add_argument("--workers", type=int, default=1,
                    help="episodes extracted at once")
    ap.add_argument("--min-confidence", default="medium",
                    choices=["low", "medium", "high"])
    args = ap.parse_args()
    if not args.all and not args.episodes:
        ap.error("pass --episodes N for a pilot, or --all")

    settings = get_settings()
    spec = ARCHIVES[args.archive]
    index_name = spec["index"](settings)
    passages = spec["passages"](settings)
    shelf = json.loads(spec["shelf"].read_text())
    # Newest first, whatever order the shelf happens to be in, so a pilot
    # of N is the N an audience is most likely to ask about.
    shelf.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    todo = shelf if args.all else shelf[:args.episodes]

    from pinecone import Pinecone
    index = Pinecone(api_key=settings.pinecone_api_key).Index(index_name)

    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    model = os.environ.get("EXTRACT_MODEL", "claude-haiku-4-5")
    store = AssetStore(index_name=index_name,
                       namespace=spec["assets"]) if args.store else None

    logger.info("%d episodes, model %s, %d at a time", len(todo), model,
                args.workers)
    every: list[dict] = []
    failed: list[str] = []
    gate = asyncio.Semaphore(max(1, args.workers))

    # Episodes side by side, each with its own windows in flight. One
    # at a time was five hours for ThreadGuy's 607, almost all of it
    # waiting on responses. A failed episode is reported and left out of
    # the cache, so a rerun picks up exactly the ones that failed; it no
    # longer takes the run, and every episode still in flight, with it.
    async def one(n: int, row: dict) -> None:
        async with gate:
            segments = await asyncio.to_thread(
                rebuild, index, passages, settings.embedding_dimension,
                row["id"])
            if not segments:
                logger.warning("  %s: no passages in the index, skipped",
                               row["id"])
                return
            episode = {"episode_id": row["id"], "title": row["title"],
                       "url": row["url"], "segments": segments}
            logger.info("[%d/%d] %s", n, len(todo), row["title"][:56])
            try:
                hits = await extract_episode(client, model, episode,
                                             args.force)
                if store is not None and hits:
                    await store.store(row["id"], row["title"], hits)
            except Exception as exc:                        # noqa: BLE001
                failed.append(row["id"])
                logger.warning("  %s failed: %s", row["id"], str(exc)[:160])
                return
            every.extend(hits)

    await asyncio.gather(*(one(n, row) for n, row in enumerate(todo, 1)))

    report = aggregate(every, min_confidence=args.min_confidence,
                       archive=args.archive)
    out = spec["out"]
    out.write_text(json.dumps(report, indent=1))
    logger.info("\n  %d assets from %d hits -> %s",
                len(report.get("assets", [])), report.get("total_hits", 0),
                out.relative_to(ROOT))

    if USAGE["calls"]:
        logger.info("  spent: %d calls, %s input tokens, %s output tokens",
                    USAGE["calls"], f"{USAGE['input_tokens']:,}",
                    f"{USAGE['output_tokens']:,}")
        logger.info("  per episode: %.0f calls, %.0f input tokens",
                    USAGE["calls"] / max(1, len(todo)),
                    USAGE["input_tokens"] / max(1, len(todo)))
    if failed:
        logger.warning("\n  %d failed, rerun to retry them: %s",
                       len(failed), " ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

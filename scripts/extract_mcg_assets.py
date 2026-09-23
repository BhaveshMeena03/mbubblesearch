"""Extract every asset discussed on MCG Live, from the index rather than
from transcripts.

    .venv/bin/python scripts/extract_mcg_assets.py --episodes 2
    .venv/bin/python scripts/extract_mcg_assets.py --all --store

Same extractor, same prompt, same model as the Market Bubble pass -- the
difference is where the words come from. That archive keeps its transcripts
in data/episodes.json; this one deliberately does not (see ingest_mcg.py),
so the only copy of the text is the metadata on the vectors themselves.

That turns out to be enough. Each MCG vector carries `text_ts`, the window
with its timestamps, and at 21,527 vectors over 1,024.7 hours they tile the
archive rather than overlapping it -- roughly one window every three
minutes, around 2,500 characters each. Rebuilding an episode is a filtered
query, a parse of the timestamps, and a sort. Nothing is re-downloaded and
nothing is re-transcribed, which is the difference between a few dollars
and a week of laptop time.

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
import re
import sys
from pathlib import Path

from anthropic import AsyncAnthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assets import aggregate  # noqa: E402
from app.assets_store import AssetStore  # noqa: E402
from app.config import anthropic_client_kwargs, get_settings  # noqa: E402

from scripts.extract_assets import USAGE, extract_episode  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mcg-assets")

SHELF = ROOT / "data" / "mcg_index.json"
OUT = ROOT / "data" / "mcg_assets.json"

# The namespace assets live in inside the MCG index. Its passages are in
# "mcg"; these are beside them, not among them.
ASSET_NAMESPACE = "assets"

# Vectors per episode. A four-hour stream windows to well under two hundred,
# and Pinecone caps a metadata-bearing query at a thousand.
MAX_WINDOWS = 1000

# "[2:11] " or "[1:00:44] " at the start of a line.
_STAMP = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$")


def seconds(stamp: str) -> float:
    """mm:ss or h:mm:ss -> seconds."""
    parts = [int(p) for p in stamp.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def to_segments(texts: list[str]) -> list[dict]:
    """Every timestamped line across an episode's windows, in order, once.

    Deduplicated on the timestamp and the opening of the line rather than on
    the line alone: a window boundary can repeat a line verbatim, and two
    genuinely different lines can share a second.
    """
    seen: set[tuple[float, str]] = set()
    out: list[dict] = []
    for text in texts:
        for line in (text or "").splitlines():
            found = _STAMP.match(line.strip())
            if not found:
                continue
            said = found.group(2).strip()
            if not said:
                continue
            at = seconds(found.group(1))
            key = (at, said[:48])
            if key in seen:
                continue
            seen.add(key)
            out.append({"t": at, "text": said})
    out.sort(key=lambda s: s["t"])
    return out


def rebuild(index, namespace: str, dimension: int, video_id: str) -> list[dict]:
    """One episode's transcript, reassembled from its own vectors."""
    probe = [0.0] * dimension
    probe[0] = 1.0                       # valid for cosine; never ranked on
    found = index.query(vector=probe, top_k=MAX_WINDOWS, namespace=namespace,
                        include_metadata=True,
                        filter={"episode_id": {"$eq": video_id}})
    windows = [(m["metadata"].get("start_seconds") or 0,
                m["metadata"].get("text_ts") or "")
               for m in found.get("matches", [])]
    windows.sort(key=lambda w: float(w[0]))
    return to_segments([w[1] for w in windows])


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

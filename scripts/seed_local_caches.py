"""Fill the local caches from Pinecone, once.

    .venv/bin/python scripts/seed_local_caches.py
    .venv/bin/python scripts/seed_local_caches.py --archive mcg --only hits

app/local_cache.py says why they exist. This is the one read that puts the
index's contents on this Mac: every stored token hit of the three
dashboards (Market Bubble, MCG, ThreadGuy), and every passage of every
episode on the three ingest shelves, read without vectors. After it,
the morning job keeps them current as it writes, and the Sunday exact-word
rebuild and the dashboards read nothing from the index at all.

Measured on 19 Oct 2026: about 25 MB for both hit stores and 240 MB for
the passages of all three archives. The same passages read with vectors,
as the Sunday job used to, were 850 MB every week.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import egress, local_cache  # noqa: E402
from app.assets_store import AssetStore  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from scripts import ingest_mcg  # noqa: E402

HITS = {"podcast": ("pinecone_index", "assets"),
        "mcg": ("mcg_pinecone_index", "assets"),
        "threadguy": ("pinecone_index", "threadguy_assets")}
ARCHIVES = sorted(set(ingest_mcg.ARCHIVES) | set(HITS))


async def seed_hits(archive: str) -> int:
    settings = get_settings()
    index_attr, namespace = HITS[archive]
    store = AssetStore(index_name=getattr(settings, index_attr), namespace=namespace)
    hits = await store.all_hits()
    by_episode: dict[str, list[dict]] = {}
    for hit in hits:
        by_episode.setdefault(str(hit.get("episode_id") or ""), []).append(hit)
    by_episode.pop("", None)
    for episode_id, rows in by_episode.items():
        local_cache.put_hits(archive, episode_id, rows)
    print(f"  {archive}: {len(hits):,} hits across {len(by_episode)} episodes")
    return len(by_episode)


def seed_windows(archive: str) -> int:
    """Every passage of every episode on the shelf, by episode, metadata
    only. One query per episode, which is the only way to read a namespace
    without its vectors."""
    settings = get_settings()
    spec = ingest_mcg.ARCHIVES[archive]
    index = PodcastIndex(namespace=spec["namespace"](settings),
                         index_name=spec["index"](settings)).index
    namespace = spec["namespace"](settings)
    probe = [0.0] * settings.embedding_dimension
    probe[0] = 1.0
    rows = ingest_mcg.shelf() if ingest_mcg.ARCHIVE == archive else None
    if rows is None:
        ingest_mcg.use(archive)
        rows = ingest_mcg.shelf()
    done = skipped = 0
    for n, row in enumerate(rows, 1):
        if local_cache.has_windows(archive, row["id"]):
            skipped += 1
            continue
        found = index.query(vector=probe, top_k=1000, namespace=namespace,
                            include_metadata=True, include_values=False,
                            filter={"episode_id": {"$eq": row["id"]}})
        matches = found.get("matches", [])
        if len(matches) >= 1000:
            # Pinecone returns at most 1000 matches with metadata. No
            # recording here is near that; if one ever is, say so rather
            # than cache a silent two thirds of it.
            print(f"\n  {archive}: {row['id']} returned 1000 passages, "
                  "the cap; its cache may be short")
        windows = [{"id": m["id"], "title": m["metadata"].get("title", ""),
                    "text": m["metadata"].get("text", "")}
                   for m in matches if m.get("metadata")]
        if windows:
            local_cache.put_windows(archive, row["id"], windows)
            done += 1
        print(f"  {archive}: {n}/{len(rows)} episodes", end="\r", flush=True)
    print(f"  {archive}: {done} episodes cached, {skipped} already there      ")
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", action="append", default=[],
                    choices=ARCHIVES)
    ap.add_argument("--only", choices=("hits", "windows"))
    args = ap.parse_args()
    archives = args.archive or ARCHIVES
    if args.only != "windows":
        for archive in archives:
            if archive in HITS:
                asyncio.run(seed_hits(archive))
    if args.only != "hits":
        for archive in archives:
            if archive in ingest_mcg.ARCHIVES:
                seed_windows(archive)
    read = egress.snapshot()
    print(f"\n  pinecone read: {read['since_start_mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

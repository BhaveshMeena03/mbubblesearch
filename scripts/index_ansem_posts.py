"""Index Ansem's own posts, in a namespace of their own.

What he said on a show and what he wrote are different claims, and the
arguments happen in the second one. "Ansem is starting to see it" answered
with "in May btw" is a question about his timeline, and the archive could
not touch it.

The namespace is separate and deliberately not wired into the bot's
routing. @mbubbleSearch's whole standing is that it answers from the
broadcast; a reply about the show sourced from a tweet would end that as
surely as one sourced from a Tesla interview would. Retrieval here is
opt-in until somebody decides otherwise.

Reads only from disk -- fetch_ansem_posts.py already paid for the posts,
and nothing here touches the X API.

    python scripts/index_ansem_posts.py --dry-run
    python scripts/index_ansem_posts.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import voyageai
from pinecone import Pinecone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.embeddings import embed_texts  # noqa: E402

POSTS = ROOT / "data" / "ansem_posts.json"
NAMESPACE = "ansem_posts"
UPSERT_BATCH = 100

# A post shorter than this is "gm", "yes", a bare handle, an emoji. It
# embeds to noise and can only dilute a search.
MIN_CHARS = 25


def worth_indexing(post: dict) -> bool:
    text = (post.get("text") or "").strip()
    if len(text) < MIN_CHARS:
        return False
    # A reply that is only handles and a link says nothing on its own.
    without = " ".join(w for w in text.split()
                       if not w.startswith("@") and not w.startswith("http"))
    return len(without.strip()) >= MIN_CHARS


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="count what would be indexed and stop")
    args = ap.parse_args()

    posts = json.loads(POSTS.read_text())
    keep = [p for p in posts if worth_indexing(p)]
    dates = sorted(p["created_at"][:10] for p in keep)
    print(f"  {len(posts)} posts on disk, {len(keep)} worth indexing")
    print(f"  span {dates[0]} .. {dates[-1]}")
    chars = sum(len(p["text"]) for p in keep)
    print(f"  {chars:,} characters (~{chars // 4:,} tokens to embed)")
    if args.dry_run:
        print("  dry run — nothing written")
        return 0

    s = get_settings()
    voyage = voyageai.AsyncClient(api_key=s.voyage_api_key)
    index = Pinecone(api_key=s.pinecone_api_key).Index(s.pinecone_index)

    vectors = await embed_texts(
        voyage, [p["text"] for p in keep],
        model=s.voyage_model,
        dimension=s.embedding_dimension,
        input_type="document",
    )
    print(f"  embedded {len(vectors)} posts")

    rows = [{
        "id": f"ansem-{p['id']}",
        "values": v,
        "metadata": {
            "post_id": p["id"],
            "text": p["text"][:4000],
            "url": p["url"],
            "created_at": p["created_at"],
            "likes": p.get("likes", 0),
            "is_reply": bool(p.get("is_reply")),
            "author": "Ansem",
        },
    } for p, v in zip(keep, vectors, strict=True)]

    for i in range(0, len(rows), UPSERT_BATCH):
        batch = rows[i:i + UPSERT_BATCH]
        await asyncio.to_thread(index.upsert, vectors=batch,
                                namespace=NAMESPACE)
        print(f"  upserted {i + len(batch)}/{len(rows)}", flush=True)

    print(f"\n  {len(rows)} posts in namespace {NAMESPACE!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

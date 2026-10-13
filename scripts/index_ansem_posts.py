"""Index Ansem's own posts, in a namespace of their own.

What he said on a show and what he wrote are different claims, and the
arguments happen in the second one. "Ansem is starting to see it" answered
with "in May btw" is a question about his timeline, and the archive could
not touch it.

The namespace is separate and deliberately not wired into the bot's
routing. @mbubbleSearch's whole standing is that it answers from the
broadcast; a reply about the show sourced from a tweet would end that as
surely as one sourced from a Tesla interview would. Since 13 October 2026
the posts are searchable on a page of their own (app/posts.py, /posts),
which is the "somebody decides otherwise" this was waiting for. The bot
and the front door still do not read them.

Each vector carries the day as a number, so "this week" can be a filter,
and whether the post is his own, a reply or a quote, because the other
half of a reply is not stored and the model has to be told. It also
writes data/posts_index.json: how many posts, first day, last day. That
file ships, and is how the page says what it covers.

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

from app import posts as posts_page  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.embeddings import embed_texts  # noqa: E402

POSTS = ROOT / "data" / "ansem_posts.json"
NAMESPACE = posts_page.NAMESPACE
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

    # Embedded as he typed it: "$ANSEM" means something to a search and
    # the forty-four characters X stores in its place do not.
    for p in keep:
        p["text"] = posts_page.readable(p["text"])
    unknown = sorted({t for p in keep for t in posts_page.unknown_tokens(p["text"])})
    if unknown:
        print(f"  {len(unknown)} token address(es) with no ticker on file, left as "
              f"they came. Look each up by address and add it to CASHTAGS in "
              f"app/posts.py: {unknown[:6]}")
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
            "day": posts_page.day_number(p["created_at"]),
            "kind": posts_page.kind_of(p),
            "likes": p.get("likes", 0),
            "is_reply": bool(p.get("is_reply")),
            "author": posts_page.AUTHOR,
        },
    } for p, v in zip(keep, vectors, strict=True)
        if posts_page.day_number(p["created_at"]) is not None]

    for i in range(0, len(rows), UPSERT_BATCH):
        batch = rows[i:i + UPSERT_BATCH]
        await asyncio.to_thread(index.upsert, vectors=batch,
                                namespace=NAMESPACE)
        print(f"  upserted {i + len(batch)}/{len(rows)}", flush=True)

    print(f"\n  {len(rows)} posts in namespace {NAMESPACE!r}")
    # Written last, after the index has them: the page reads this to say
    # what it covers and to know which day "the newest post" is.
    days = sorted(r["metadata"]["created_at"][:10] for r in rows)
    posts_page.COVERAGE.write_text(json.dumps({
        "author": posts_page.AUTHOR, "handle": posts_page.HANDLE,
        "count": len(rows), "first": days[0], "last": days[-1],
        "kinds": {k: sum(1 for r in rows if r["metadata"]["kind"] == k)
                  for k in posts_page.KINDS},
    }, indent=1) + "\n")
    print(f"  wrote {posts_page.COVERAGE.name}: {days[0]} .. {days[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

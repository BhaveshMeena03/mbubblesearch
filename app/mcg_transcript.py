"""An MCG episode's transcript, rebuilt from the vectors that hold it.

ingest_mcg.py embeds the text and throws it away, which is the right trade
for a thousand hours: the shelf holds titles and durations, Pinecone holds
the words. So anything here that needs a transcript -- captions for a clip,
an asset pass, an index of who was on -- has to ask the index for it back.

It comes back whole. The 21,527 vectors tile 1,024.7 hours rather than
overlapping, roughly one window every three minutes, so a filtered query
plus a sort reproduces the episode: the 222-minute Bitcoin-at-85K stream
returns 2,823 lines spanning the full 222 minutes.

This lives in app/ rather than in a script because the server needs it too.
It was written in scripts/extract_mcg_assets.py first, and having the only
copy there is what would force the clip route to import from a script.
"""

from __future__ import annotations

import re

from .podcast import _stamped

# Vectors per episode. A four-hour stream windows to well under two
# hundred, and Pinecone caps a metadata-bearing query at a thousand.
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

    Deduplicated on the timestamp and the opening of the line rather than
    on the line alone: a window boundary can repeat a line verbatim, and
    two genuinely different lines can share a second.
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
    """One episode's transcript, reassembled from its own vectors.

    Both metadata shapes are read, through podcast._stamped: interviews
    carry `text_ts`, the stamped copy, while streams carry `text` beside a
    comma-separated `line_times`, which is a hundred bytes against a
    duplicate of every passage. Reading only the first skipped 230 of 645
    episodes -- 727 hours, every one a stream -- and looked exactly like an
    archive where nobody had said anything.
    """
    probe = [0.0] * dimension
    probe[0] = 1.0                       # valid for cosine; never ranked on
    found = index.query(vector=probe, top_k=MAX_WINDOWS, namespace=namespace,
                        include_metadata=True,
                        filter={"episode_id": {"$eq": video_id}})
    windows = [(m["metadata"].get("start_seconds") or 0,
                _stamped(m["metadata"]))
               for m in found.get("matches", [])]
    windows.sort(key=lambda w: float(w[0]))
    return to_segments([w[1] for w in windows])

"""What the morning job already has in hand, kept on this Mac.

On 19 Oct 2026 the Pinecone project hit its 10 GB monthly read cap and
every search on five archives failed for a day. Visitors' searches were
15 MB of that. The rest was the batch work reading its own output back
out of the index: the token dashboards loading every stored hit at each
boot and each refresh (32 MB a time, seventy-odd deploys), and the Sunday
exact-word rebuild fetching every passage of two archives (850 MB a
Sunday) to get at text the ingest had held in memory six days earlier.

So the morning job keeps a copy of what it writes, here, one small file
per episode, and the dashboards and the term index read these. Nothing
here is the record: Pinecone is, and scripts/seed_local_caches.py fills
these from it once, or again after a lost laptop. Gitignored, because the
app on Render serves committed report files instead and never needs them.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HITS = ROOT / "data" / ".hits_cache"
WINDOWS = ROOT / "data" / ".windows_cache"


def _safe(episode_id: str) -> str:
    """An episode id as a file name. YouTube ids and "x-<post>" ids are
    already safe; anything else is spelled out so it cannot escape the
    folder."""
    return "".join(c if c.isalnum() or c in "-_" else f"%{ord(c):02x}"
                   for c in str(episode_id))


# --- hits: one episode's token mentions, as the extractor produced them ----

def put_hits(archive: str, episode_id: str, hits: list[dict]) -> Path:
    folder = HITS / archive
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe(episode_id)}.json"
    path.write_text(json.dumps(hits, separators=(",", ":")))
    return path


def load_hits(archive: str) -> dict[str, list[dict]]:
    """Every cached episode's hits, by episode id."""
    folder = HITS / archive
    if not folder.is_dir():
        return {}
    out: dict[str, list[dict]] = {}
    for path in sorted(folder.glob("*.json")):
        hits = json.loads(path.read_text())
        episode_id = (hits[0].get("episode_id") if hits else None) or path.stem
        out[episode_id] = hits
    return out


# --- windows: one episode's passages as they were embedded ----------------

def put_windows(archive: str, episode_id: str, windows: list[dict]) -> Path:
    """windows: [{"id", "title", "text"}], the same ids as the vectors."""
    folder = WINDOWS / archive
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe(episode_id)}.json.gz"
    path.write_bytes(gzip.compress(
        json.dumps(windows, separators=(",", ":")).encode(), mtime=0))
    return path


def has_windows(archive: str, episode_id: str) -> bool:
    return (WINDOWS / archive / f"{_safe(episode_id)}.json.gz").is_file()


def load_windows(archive: str, episode_id: str) -> list[dict]:
    path = WINDOWS / archive / f"{_safe(episode_id)}.json.gz"
    if not path.is_file():
        return []
    return json.loads(gzip.decompress(path.read_bytes()))


def forget(archive: str, episode_id: str) -> None:
    """An episode taken out of the index comes out of here too, or a
    re-transcription would sit beside its own broken copy."""
    for path in (HITS / archive / f"{_safe(episode_id)}.json",
                 WINDOWS / archive / f"{_safe(episode_id)}.json.gz"):
        path.unlink(missing_ok=True)

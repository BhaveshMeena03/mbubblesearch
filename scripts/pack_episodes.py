#!/usr/bin/env python3
"""Compress a transcript shelf into the copy the image ships.

    python scripts/pack_episodes.py                          # episodes.json
    python scripts/pack_episodes.py data/tradfi_episodes.json


Run this after an ingest. episodes.json is 7.3MB and rewritten every time
new episodes land, so committing it would put a fresh 7MB blob in git on
each pass; it stays gitignored and this 2.3MB gzip is what gets committed
and copied into the container.

The clipper is the only thing that reads it at runtime — it needs the
source URL to download a section and the per-second segments to build
captions, and neither exists anywhere else at runtime. Forgetting to run
this means newly ingested episodes return 404 from the clip endpoint;
nothing else degrades.
"""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "data" / "episodes.json"


def main() -> int:
    # A second archive ships the same way for the same reason, so the
    # path is an argument rather than a copy of this file. Defaulting to
    # episodes.json keeps every existing caller working unchanged.
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not raw.is_absolute():
        raw = ROOT / raw
    out = raw.with_suffix(".json.gz")
    if not raw.exists():
        print(f"no {raw} — nothing to pack", file=sys.stderr)
        return 1

    # Parsed rather than streamed straight through, so a truncated or
    # half-written shelf fails here instead of shipping and failing in
    # the container.
    episodes = json.loads(raw.read_text())
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(episodes, fh, separators=(",", ":"))

    print(f"  {len(episodes)} recordings")
    print(f"  {raw.stat().st_size / 1e6:.1f}MB -> "
          f"{out.stat().st_size / 1e6:.1f}MB  ({out.relative_to(ROOT)})")
    print(f"  commit it: git add -f {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

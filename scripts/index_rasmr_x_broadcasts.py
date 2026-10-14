"""Index rasmr broadcasts that ran only on X, straight from X.

    .venv/bin/python scripts/index_rasmr_x_broadcasts.py 1vJpPNAVXlaJE ...

Named by broadcast id, on purpose. Whether a broadcast is X-only is a
judgement: one with no pair by ear may still be the same show as an
unpaired YouTube stream (same title, a dropped connection between the two
listening points), and indexing that would put the same stream in twice.
So the ids are chosen by a person from data/rasmr_x_broadcasts.json.

The same steps as scripts/ingest_mcg.py for a video: download the audio,
transcribe on Groq, drop Whisper's hallucinated filler, refuse a
transcript that stops short or a broadcast under twenty minutes, embed
into the `rasmr` namespace, and only then add the shelf row. Filed as
"x-<post id>", the way Market Bubble's X broadcasts are, with the player
as its url, which opens at a second with ?t=.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from app.provenance import drop_hallucinated  # noqa: E402
from app.schemas import Episode  # noqa: E402
from scripts import ingest_mcg as ingest  # noqa: E402

BROADCASTS = ROOT / "data" / "rasmr_x_broadcasts.json"
SHORTEST = 20 * 60


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=False)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


async def main(wanted: set[str]) -> int:
    ingest.use("rasmr")
    ingest.CHUNK_WORKERS = 3
    casts = {c["broadcast"].rsplit("/", 1)[-1]: c
             for c in json.loads(BROADCASTS.read_text())["broadcasts"]}
    unknown = sorted(wanted - set(casts))
    if unknown:
        print(f"  not in {BROADCASTS.name}: {', '.join(unknown)}")
        return 1
    shelved = {r["id"] for r in ingest.shelf()}
    index = PodcastIndex(namespace="rasmr", index_name=get_settings().pinecone_index)
    work = Path("/tmp/rasmr_audio")
    work.mkdir(exist_ok=True)
    added = 0
    for bid in sorted(wanted):
        cast = casts[bid]
        eid = f"x-{cast['post']}"
        if eid in shelved:
            print(f"  {bid}: already on the shelf as {eid}")
            continue
        path = work / f"{bid}.m4a"
        run = subprocess.run([str(ROOT / ".venv" / "bin" / "yt-dlp"), "--no-warnings", "-q",
                              "-f", "bestaudio/best", "--extract-audio", "--audio-format", "m4a",
                              "--concurrent-fragments", "8", "-o", str(path), cast["broadcast"]],
                             capture_output=True, text=True, timeout=7200, check=False)
        if not path.exists():
            print(f"  {bid}: download failed: {(run.stderr.strip().splitlines() or ['?'])[-1][:140]}")
            continue
        seconds = duration(path)
        if seconds < SHORTEST:
            print(f"  {bid}: {seconds / 60:.0f} minutes, too short to be a stream; skipped")
            path.unlink(missing_ok=True)
            continue
        try:
            segments = ingest.transcribe_via_groq(path, ingest.groq_key())
        finally:
            path.unlink(missing_ok=True)
        kept, _dropped = drop_hallucinated(segments)
        short = ingest.shortfall(kept, seconds)
        if short:
            print(f"  {bid}: {short}; not shelved")
            continue
        day = cast["posted"][:10]
        title = cast.get("title") or f"Live on X, {day}"
        episode = Episode(episode_id=eid, title=title, url=cast["broadcast"],
                          platform="other", published_at=day, segments=kept)
        windows = await index.ingest([episode])
        ingest.add_to_shelf({"id": eid, "title": title, "url": cast["broadcast"],
                             "published_at": day, "seconds": int(seconds),
                             "format": "stream", "source": "x"})
        added += 1
        print(f"  {bid} -> {eid}  {seconds / 3600:.1f}h  {windows} passages")
    print(f"\n  added {added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(set(sys.argv[1:]))))

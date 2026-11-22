"""Find the video file behind each of Ansem's own X videos.

    .venv/bin/python scripts/find_x_videos.py                # the ones not on file
    .venv/bin/python scripts/find_x_videos.py --all          # look every one up again
    .venv/bin/python scripts/find_x_videos.py --episode x-…  # one

Writes data/x_videos.json, which the page reads to play one of his videos
from a second (app/x_videos.py says why it has to be the file). Which
rows are his own comes from the summaries that ship, so this runs after
scripts/pack_summaries.py in the morning job.

One lookup a video through yt-dlp, which reads the same public data X's
own player does: no X API credit. A video it cannot find a file for is
said so, and the script ends on 1 so the job's log shows it. A post with
no file to find is written down and not asked for again; one whose lookup
failed is tried on the next two runs and then left, so a deleted post
fails the job three times and not every morning for good.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import x_videos  # noqa: E402
from app.schemas import his_own  # noqa: E402
from app.summaries import load_shipped  # noqa: E402

TRIES = 3


def own_videos() -> dict[str, str]:
    """episode id -> the post, for every video he posted himself."""
    return {row["episode_id"]: row["url"] for row in load_shipped().values()
            if his_own(row.get("show")) and str(row.get("url") or "").startswith("https://x.com/")}


def look_up(url: str) -> dict | None:
    import yt_dlp
    options = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as reader:
        return x_videos.pick(reader.extract_info(url, download=False))


def save(rows: dict) -> None:
    """Whole or not at all."""
    fresh = x_videos.PATH.with_suffix(".json.tmp")
    fresh.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    fresh.replace(x_videos.PATH)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="look every video up again")
    ap.add_argument("--episode", help="only this one")
    args = ap.parse_args()

    posts = own_videos()
    if not posts:
        # The summaries could not be read, or hold none of his videos.
        # Either way nothing is known, and writing that down would take
        # every video on file off the site.
        print("  none of his videos found in the summaries: the file is left as it is")
        return 1
    try:
        rows = json.loads(x_videos.PATH.read_text())
    except (FileNotFoundError, ValueError):
        rows = {}
    if not isinstance(rows, dict):
        rows = {}

    def due(episode: str) -> bool:
        row = rows.get(episode)
        if args.all or args.episode == episode or not isinstance(row, dict):
            return True
        return "mp4" not in row and "none" not in row and int(row.get("failed") or 0) < TRIES

    wanted = [e for e in posts if due(e) and args.episode in (None, e)]
    missed = 0
    for episode in wanted:
        before = rows.get(episode) if isinstance(rows.get(episode), dict) else {}
        try:
            found = look_up(posts[episode])
        except Exception as exc:                                # noqa: BLE001
            missed += 1
            rows[episode] = {"failed": int(before.get("failed") or 0) + 1}
            print(f"  {episode}: {type(exc).__name__}, its times stay as text")
            continue
        if found:
            rows[episode] = found
            print(f"  {episode}: found")
        else:
            missed += 1
            rows[episode] = {"none": datetime.now(UTC).date().isoformat()}
            print(f"  {episode}: no file to find, its times stay as text")
    # A video that is no longer one of his is not kept.
    rows = {episode: row for episode, row in rows.items() if episode in posts}
    save(rows)
    playable = sum(1 for row in rows.values() if isinstance(row, dict) and "mp4" in row)
    print(f"  {playable} of {len(posts)} of his videos have a file")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())

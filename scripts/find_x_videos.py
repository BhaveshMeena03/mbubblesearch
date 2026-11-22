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
left out and said so, and the script ends on 1 so the job's log shows it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import x_videos  # noqa: E402
from app.schemas import his_own  # noqa: E402
from app.summaries import load_shipped  # noqa: E402


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
    try:
        rows = json.loads(x_videos.PATH.read_text())
    except (FileNotFoundError, ValueError):
        rows = {}
    wanted = [e for e in posts if args.all or args.episode == e or e not in rows]
    if args.episode:
        wanted = [e for e in wanted if e == args.episode]
    missed = 0
    for episode in wanted:
        try:
            found = look_up(posts[episode])
        except Exception as exc:                                # noqa: BLE001
            print(f"  {episode}: {type(exc).__name__}")
            found = None
        if found:
            rows[episode] = found
            print(f"  {episode}: found")
        else:
            missed += 1
            print(f"  {episode}: no file found, its times stay as text")
    # A video that is no longer one of his is not kept.
    rows = {episode: row for episode, row in rows.items() if episode in posts}
    save(rows)
    print(f"  {len(rows)} of {len(posts)} of his videos have a file")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())

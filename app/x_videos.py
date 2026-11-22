"""The video file behind a video Ansem posted on X, so a page can play it
from a second.

X has no link that opens a posted video at a time: a status URL starts at
the beginning whatever ?t= says (app/x_posts.py). The file itself can be
played from any second, inside the page. X's video server answers for it
only when the request does not name the site it came from, so the page
asks from a frame that names none (demo/podcast.html, playFile).

He chose this on 22 November 2026 knowing what it costs: it goes around a
block X put up, X can close it any day, and a play here is not a view on
the post. If it closes, the page says so and links the post.

data/x_videos.json is written by scripts/find_x_videos.py, one row for
each of his own videos: episode id -> {"mp4", "small", "poster"}. Nothing
in it is trusted on the way out. Only an address on X's own video and
image hosts is ever handed to a page.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

PATH = Path(__file__).resolve().parent.parent / "data" / "x_videos.json"

FILE = re.compile(r"https://video\.twimg\.com/[A-Za-z0-9_\-./]+\.mp4(?:\?tag=\d{1,4})?")
POSTER = re.compile(r"https://pbs\.twimg\.com/[A-Za-z0-9_\-./]+\.(?:jpg|png)"
                    r"(?:\?name=[a-z0-9]{1,12})?")

# What a laptop plays, and what a phone does. A file taller than the
# first is a hundred megabytes for a quarter of an hour.
TALL, SMALL = 720, 360


def pick(info: object) -> dict | None:
    """The files worth keeping out of what yt-dlp found for a post.

    Only a whole file X serves over https: the page plays it with the
    browser's own player, which Chrome cannot do with a playlist. The
    tallest at or under 720 lines, and the tallest at or under 360 for a
    phone; when every file is taller than the limit, the shortest.
    """
    if not isinstance(info, dict):
        return None
    files = []
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        url, height = fmt.get("url"), fmt.get("height")
        if (isinstance(url, str) and FILE.fullmatch(url)
                and isinstance(height, int) and height > 0):
            files.append((height, url))
    if not files:
        return None
    files.sort()

    def under(limit: int) -> str:
        fits = [url for height, url in files if height <= limit]
        return fits[-1] if fits else files[0][1]

    found = {"mp4": under(TALL), "small": under(SMALL)}
    poster = info.get("thumbnail")
    if isinstance(poster, str) and POSTER.fullmatch(poster):
        found["poster"] = poster
    return found


@lru_cache(maxsize=1)
def _rows() -> dict:
    try:
        rows = json.loads(PATH.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("could not read x_videos.json")
        return {}
    return rows if isinstance(rows, dict) else {}


def for_episode(episode_id: object) -> dict | None:
    """What a page may play for this episode, or None."""
    row = _rows().get(episode_id) if isinstance(episode_id, str) else None
    if not isinstance(row, dict):
        return None
    mp4 = row.get("mp4")
    if not isinstance(mp4, str) or not FILE.fullmatch(mp4):
        return None
    out = {"mp4": mp4}
    small, poster = row.get("small"), row.get("poster")
    if isinstance(small, str) and FILE.fullmatch(small):
        out["small"] = small
    if isinstance(poster, str) and POSTER.fullmatch(poster):
        out["poster"] = poster
    return out


def attach(hit: dict) -> dict:
    """A search passage, with its video's file when it is from one of
    these: the page then plays the passage where it is cited, at its
    second, as it does a passage that is on YouTube."""
    file = for_episode(hit.get("episode_id"))
    return {**hit, "file": file} if file else hit

"""Which X posts are recordings in these archives, and where a second of
each one sits in X's own player.

Read by the browser extension in extension/. It puts a button on any post
that is one of these; the button opens the summary and an ask box, and a
time in either moves the player on that post to the second. A link can't
do that for a posted video: X opens a status URL at the start whatever
?t= says. The extension can set the player's own clock, checked on the
Episode 24 replay on 31 Oct 2026, where 1:18:09 landed on 4689.0.

Three kinds of post, all from files the service already ships:

  Market Bubble on X   indexed from the X recording itself as "x-<post>",
                       so a second in the transcript is a second on X.
                       Ansem's own videos are the same.
  MCG and rasmr        indexed from YouTube, live on both at once. The
                       pair files say which post carries each show and how
                       far X's clock is from YouTube's (app/mcg_on_x.py).
  rasmr X-only         streams that never reached YouTube, indexed from X
                       as "x-<post>" like Market Bubble's.

Every time the extension shows is converted here, never in the browser,
so there is one copy of the clock arithmetic and it is the one the bot's
links already use.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from . import mcg_on_x

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"
SUMMARIES = _DATA / "summaries.json.gz"
BROADCAST_LINKS = _DATA / "broadcast_links.json"
MCG_PAIRS = _DATA / "mcg_broadcast_links.json"
RASMR_PAIRS = _DATA / "rasmr_broadcast_links.json"
RASMR_SHELF = _DATA / "rasmr_index.json"
MCG_SHELF = _DATA / "mcg_index.json"

POST_ID = re.compile(r"^[0-9]{5,25}$")

ARCHIVE_NAMES = {"podcast": "Market Bubble", "mcg": "MCG Live",
                 "rasmr": "rasmr"}

# Where each archive's page is, for the "open on the site" link. Built
# here and nowhere else: the extension opens no address it was sent.
SITE = "https://search.lexthedev.com"
_PAGES = {"podcast": "/demo/podcast.html", "mcg": "/demo/mcg.html",
          "rasmr": "/demo/rasmr.html"}

# h:mm:ss or m:ss, the way summaries and answers write times. Two digits
# after every colon, so "3:1" in a ratio is not a time.
TIME = re.compile(r"(?<![\d:])(\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])")


def _json(path: Path):
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        return json.loads(path.read_text())
    except Exception:                                       # noqa: BLE001
        # One unreadable file costs that archive its buttons, not the
        # others theirs.
        logger.warning("x posts: could not read %s", path.name)
        return None


def _post_of(episode_id: str) -> str | None:
    post = episode_id[2:] if episode_id.startswith("x-") else ""
    return post if POST_ID.match(post) else None


@lru_cache(maxsize=1)
def _summaries() -> dict[str, dict]:
    rows = _json(SUMMARIES) or []
    return {r["episode_id"]: r for r in rows
            if isinstance(r, dict) and r.get("episode_id")}


def _rows(path: Path) -> list[dict]:
    rows = _json(path) or []
    return rows if isinstance(rows, list) else rows.get("episodes", [])


@lru_cache(maxsize=1)
def shelf() -> dict[str, dict]:
    """Post id to what it is: archive, episode, and the clock to X."""
    posts: dict[str, dict] = {}
    titled = {str(r.get("id")): r for path in (MCG_SHELF, RASMR_SHELF)
              for r in _rows(path)}

    # Market Bubble: every episode indexed from X. The summaries file has
    # each one with its title; the broadcast list catches any without.
    known = set(_summaries()) | set(_json(BROADCAST_LINKS) or {})
    for episode_id in sorted(known):
        post = _post_of(episode_id)
        if post:
            posts[post] = {"archive": "podcast", "episode_id": episode_id,
                           "clock": {"offset": 0}}

    for archive, path in (("mcg", MCG_PAIRS), ("rasmr", RASMR_PAIRS)):
        for video, pair in (_json(path) or {}).items():
            post = str(pair.get("post", ""))
            if not POST_ID.match(post):
                continue
            if pair.get("sections"):
                clock = {"sections": pair["sections"]}
            elif "offset" in pair:
                clock = {"offset": pair["offset"]}
            else:
                # Paired, but the clocks were never measured: the post
                # gets its button and its answers, and no time on it moves
                # the player, because a guessed second is a wrong one.
                clock = {}
            shown = titled.get(video, {})
            posts[post] = {"archive": archive, "episode_id": video,
                           "clock": clock, "title": shown.get("title"),
                           "published_at": shown.get("published_at")}

    # Last, so a stream indexed from X wins over its YouTube twin's pair:
    # its transcript is on X's clock already, measured rather than offset.
    for row in _rows(RASMR_SHELF):
        post = _post_of(str(row.get("id", "")))
        if post:
            posts[post] = {"archive": "rasmr", "episode_id": row["id"],
                           "clock": {"offset": 0}, "title": row.get("title"),
                           "published_at": row.get("published_at")}
    return posts


def to_x(seconds: int, clock: dict) -> int | None:
    """A second of the transcript as a second of the X post, or None
    where the two clocks are not known to agree."""
    if not clock:
        return None
    gap = mcg_on_x._offset(clock, seconds)
    if gap is None or seconds + gap < 0:
        # Before X's stream began: said on YouTube, never aired on X.
        return None
    return seconds + gap


def _seconds(token: str) -> int:
    parts = [int(p) for p in token.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


# A time of day, not a moment in the recording: "CPI prints at 8:30 ET",
# "the open at 9:30am". Shown as words, because a button would play
# minute eight of the show.
_CLOCK_OF_DAY = re.compile(
    r"\s?(?:a\.?m\b\.?|p\.?m\b\.?|(?:et|est|edt|ct|cst|cdt|mt|pt|pst|pdt|utc|gmt|ist|bst|cet)\b)",
    re.I)


def times_in(text: str, clock: dict) -> dict[str, int]:
    """Every time written in `text`, to the X second it plays at.

    Keyed by the time exactly as written, so the extension finds each one
    by the characters it sees and never parses a clock itself. A time
    whose minutes or seconds are 60 or more is not a time, and neither is
    a time of day.
    """
    out: dict[str, int] = {}
    text = text or ""
    day_times = {f.group(1) for f in TIME.finditer(text)
                 if _CLOCK_OF_DAY.match(text, f.end())}
    for found in TIME.finditer(text):
        token = found.group(1)
        if any(int(p) >= 60 for p in token.split(":")[1:]):
            continue
        # Every copy of a time of day stays words: the extension finds
        # times by their characters, so "8:30" cannot be a button in one
        # place and the clock in another.
        at = to_x(_seconds(token), clock)
        if at is not None and token not in day_times:
            out[token] = at
    return out


def listing() -> dict[str, str]:
    """Post id to archive. All the extension downloads to decide where a
    button goes, so nothing about what anyone scrolls past leaves their
    browser."""
    return {post: row["archive"] for post, row in shelf().items()}


def page_for(archive: str, episode_id: str) -> str:
    if archive == "podcast":
        return f"{SITE}{_PAGES[archive]}#ep-{episode_id}"
    return f"{SITE}{_PAGES[archive]}"


def describe(post: str) -> dict | None:
    """One post as the extension's panel shows it, or None."""
    row = shelf().get(post)
    if row is None:
        return None
    archive, episode_id = row["archive"], row["episode_id"]
    summary = _summaries().get(episode_id) if archive == "podcast" else None
    text = (summary or {}).get("summary") or None
    return {
        "post": post,
        "archive": archive,
        "archive_name": ARCHIVE_NAMES[archive],
        "episode_id": episode_id,
        "title": (summary or {}).get("title") or row.get("title") or "",
        "show": (summary or {}).get("show") or None,
        "published_at": ((summary or {}).get("published_at")
                         or row.get("published_at") or None),
        "summary": text,
        "times": times_in(text or "", row["clock"]),
        "seekable": bool(row["clock"]),
        "page": page_for(archive, episode_id),
    }

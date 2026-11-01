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

And two that are not a whole recording. Clips: Ansem posts a minute
of Market Bubble or of his own videos several times a day. Each one
placed in the archive by its own words (scripts/place_x_clips.py, written
to data/x_clips.json) gets a panel saying where it is from and what was
said right after the cut. A time inside the clip moves the clip's player;
any other opens the whole show at that second. And link posts: ours,
which link a show's X broadcast at the second rather than upload a clip
(scripts/place_x_links.py reads the second off each link, written to
data/x_links.json). The panel names the moment and the show, and every
time in it opens the broadcast there.

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

from . import mcg_on_x, podcast

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"
SUMMARIES = _DATA / "summaries.json.gz"
BROADCAST_LINKS = _DATA / "broadcast_links.json"
MCG_PAIRS = _DATA / "mcg_broadcast_links.json"
RASMR_PAIRS = _DATA / "rasmr_broadcast_links.json"
RASMR_SHELF = _DATA / "rasmr_index.json"
MCG_SHELF = _DATA / "mcg_index.json"
CLIPS = _DATA / "x_clips.json"
LINKS = _DATA / "x_links.json"
MCG_SUMMARIES = _DATA / "mcg_summaries.json.gz"
THREADGUY_PAIRS = _DATA / "threadguy_broadcast_links.json"
THREADGUY_SHELF = _DATA / "threadguy_index.json"
THREADGUY_SUMMARIES = _DATA / "threadguy_summaries.json.gz"

POST_ID = re.compile(r"^[0-9]{5,25}$")
BROADCAST = re.compile(r"^https://x\.com/i/broadcasts/([A-Za-z0-9]{8,20})$")
EPISODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# The whole show at a second, in the three shapes the site's own links take
# for this archive: X's broadcast player, an X post, a YouTube video. Any
# other is dropped here, and the extension checks again with the same
# pattern (tests/test_x_extension.py holds them to it).
LINK = re.compile(
    r"^https://(?:x\.com/i/broadcasts/[A-Za-z0-9]{8,20}\?t=[0-9]{1,6}"
    r"|x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{5,25}\?t=[0-9]{1,6}"
    r"|www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&t=[0-9]{1,6}s)$")

# The two of those that open at the second. X opens a post's video at its
# start whatever ?t= says, so a time is never a link to one: 12 of the
# first 31 clips came from Ansem's uploads, and "watch from 0:12:26" would
# have started them at 0:00.
SEEKS = re.compile(
    r"^https://(?:x\.com/i/broadcasts/[A-Za-z0-9]{8,20}\?t=[0-9]{1,6}"
    r"|www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&t=[0-9]{1,6}s)$")

# Whose clips are placed: the show's own accounts, ours, and the clippers
# we follow. Anyone's audio can match the archive, but the panel says "This
# clip is from" over the video, and over a stranger's post that would vouch
# for whatever they cut around it. Lowercase: X handles are not case
# sensitive, and yt-dlp hands back whichever case the account chose.
# The clippers were found on 2 Nov 2026 by testing every show-related video
# from a week of X search against the archive: OrderBookShow had 4 that
# matched, BullClips and insentosprime 1 each, all clip accounts by trade.
CLIP_ACCOUNTS = frozenset({"blknoiz06", "marketbubble", "mbubblesearch", "kaiz_294",
                           "orderbookshow", "bullclips", "insentosprime"})


def clip_account(handle) -> bool:
    return isinstance(handle, str) and handle.lower() in CLIP_ACCOUNTS

# A clip's own player covers its words; a time a few seconds past the last
# one is still the clip, a time beyond that is the whole show's.
CLIP_SLACK_SECONDS = 5
_DAY = 24 * 3600

ARCHIVE_NAMES = {"podcast": "Market Bubble", "mcg": "MCG Live",
                 "rasmr": "rasmr", "threadguy": "ThreadGuy"}

# Where each archive's page is, for the "open on the site" link. Built
# here and nowhere else: the extension opens no address it was sent.
SITE = "https://search.lexthedev.com"
_PAGES = {"podcast": "/demo/podcast.html", "mcg": "/demo/mcg.html",
          "rasmr": "/demo/rasmr.html", "threadguy": "/demo/threadguy.html"}

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
    return post if POST_ID.fullmatch(post) else None


@lru_cache(maxsize=1)
def _summaries() -> dict[str, dict]:
    rows = _json(SUMMARIES) or []
    return {r["episode_id"]: r for r in rows
            if isinstance(r, dict) and r.get("episode_id")}


@lru_cache(maxsize=1)
def _mcg_summaries() -> dict[str, dict]:
    given = _json(MCG_SUMMARIES) or {}
    return given if isinstance(given, dict) else {}


@lru_cache(maxsize=1)
def _threadguy_summaries() -> dict[str, dict]:
    given = _json(THREADGUY_SUMMARIES) or {}
    return given if isinstance(given, dict) else {}


def _mcg_markdown(episode_id: str) -> str | None:
    return _notes_markdown(_mcg_summaries(), episode_id)


def _notes_markdown(store: dict, episode_id: str) -> str | None:
    """An MCG or ThreadGuy summary in the markdown the panel reads, its
    topic times written h:mm:ss on YouTube's clock, which times_in moves to
    X's.

    Only a summary marked "pinned" is shown: one whose every topic time is
    a line of the show, as the writers have checked since 28 Oct 2026.
    Older ones could run up to 35 minutes late, and a button here moves the
    video to the time, so a late one plays the wrong words. A show whose
    summary isn't pinned keeps its ask box and gets no summary.
    """
    row = store.get(episode_id)
    if (not isinstance(row, dict) or row.get("pinned") is not True
            or not isinstance(row.get("tldr"), str) or not row["tldr"]):
        return None
    lines = [f"**TL;DR** {row['tldr'].strip()}", "", "**Topics**"]
    for topic in row.get("topics") or []:
        at = _second(topic.get("t")) if isinstance(topic, dict) else None
        if at is not None and isinstance(topic.get("text"), str):
            lines.append(f"- [{clock_text(at)}] {topic['text'].strip()}")
    return "\n".join(lines)


def _rows(path: Path) -> list[dict]:
    rows = _json(path) or []
    return rows if isinstance(rows, list) else rows.get("episodes", [])


def _second(value) -> int | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value < _DAY
    return value if ok else None


@lru_cache(maxsize=1)
def _clips() -> dict[str, dict]:
    """data/x_clips.json, each row checked. A row that is wrong in any
    way is left out whole, the post then has no button, rather than shown
    with a guessed part."""
    out: dict[str, dict] = {}
    given = _json(CLIPS) or {}
    if not isinstance(given, dict):
        return out
    for post, row in given.items():
        if not (POST_ID.fullmatch(str(post)) and isinstance(row, dict)):
            continue
        start, end = _second(row.get("start")), _second(row.get("end"))
        after_at = row.get("after_at")
        if (row.get("archive") != "podcast" or not clip_account(row.get("account"))
                or not EPISODE_ID.fullmatch(str(row.get("episode_id", "")))
                or start is None or end is None or end < start
                or (after_at is not None and _second(after_at) is None)
                or not isinstance(row.get("before", ""), str)
                or not isinstance(row.get("after", ""), str)):
            logger.warning("x posts: clip %s is malformed, left out", post)
            continue
        out[str(post)] = {"episode_id": row["episode_id"], "start": start, "end": end,
                          "before": row.get("before", "")[:1500],
                          "after": row.get("after", "")[:3000],
                          "after_at": after_at}
    return out


@lru_cache(maxsize=1)
def shelf() -> dict[str, dict]:
    """Post id to what it is: archive, episode, and the clock to X."""
    posts: dict[str, dict] = {}
    titled = {str(r.get("id")): r for path in (MCG_SHELF, RASMR_SHELF, THREADGUY_SHELF)
              for r in _rows(path)}

    # Market Bubble: every episode indexed from X. The summaries file has
    # each one with its title; the broadcast list catches any without.
    known = set(_summaries()) | set(_json(BROADCAST_LINKS) or {})
    for episode_id in sorted(known):
        post = _post_of(episode_id)
        if post:
            posts[post] = {"archive": "podcast", "episode_id": episode_id,
                           "clock": {"offset": 0}}

    for archive, path in (("mcg", MCG_PAIRS), ("rasmr", RASMR_PAIRS),
                          ("threadguy", THREADGUY_PAIRS)):
        for video, pair in (_json(path) or {}).items():
            post = str(pair.get("post", ""))
            if not POST_ID.fullmatch(post):
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

    # Clips, and never over a post that is a whole recording: that one
    # gets the whole recording's panel.
    for post, clip in _clips().items():
        if post not in posts:
            posts[post] = {"archive": "podcast", "episode_id": clip["episode_id"],
                           "kind": "clip", "clock": {}, "clip": clip}

    # Link posts, last: a broadcast this archive knows, at a second that
    # maps onto its transcript. A link to a show we don't hold, or into
    # minutes X's stream missed, gets no button.
    shows = _broadcasts()
    for post, link in _links().items():
        show = shows.get(link["id"])
        at = from_x(link["t"], show["clock"]) if show else None
        if post in posts or at is None:
            continue
        shown = titled.get(show["episode_id"], {})
        posts[post] = {"archive": show["archive"], "episode_id": show["episode_id"],
                       "kind": "link", "clock": show["clock"],
                       "title": shown.get("title"), "published_at": shown.get("published_at"),
                       "link": {"broadcast": show["url"], "t": link["t"], "at": at}}
    return posts


def _broadcast_id(url) -> str | None:
    found = BROADCAST.fullmatch(str(url or "").split("?", 1)[0])
    return found.group(1) if found else None


def _clock_of(pair: dict) -> dict:
    if pair.get("sections"):
        return {"sections": pair["sections"]}
    if "offset" in pair:
        return {"offset": pair["offset"]}
    return {}


@lru_cache(maxsize=1)
def _broadcasts() -> dict[str, dict]:
    """Every show this archive knows on X, by broadcast id: its archive,
    its episode, and the clock between the two."""
    out: dict[str, dict] = {}
    for episode_id, url in (_json(BROADCAST_LINKS) or {}).items():
        bid = _broadcast_id(url)
        if bid and _post_of(episode_id):
            out[bid] = {"archive": "podcast", "episode_id": episode_id,
                        "clock": {"offset": 0}, "url": f"https://x.com/i/broadcasts/{bid}"}
    for archive, path in (("mcg", MCG_PAIRS), ("rasmr", RASMR_PAIRS),
                          ("threadguy", THREADGUY_PAIRS)):
        for video, pair in (_json(path) or {}).items():
            bid = _broadcast_id(pair.get("broadcast") if isinstance(pair, dict) else None)
            clock = _clock_of(pair) if bid else {}
            if bid and clock:
                out[bid] = {"archive": archive, "episode_id": video, "clock": clock,
                            "url": f"https://x.com/i/broadcasts/{bid}"}
    return out


@lru_cache(maxsize=1)
def _links() -> dict[str, dict]:
    """data/x_links.json, each row checked: post id to the broadcast it
    links and the second it links to."""
    out: dict[str, dict] = {}
    given = _json(LINKS) or {}
    for post, row in (given.items() if isinstance(given, dict) else ()):
        if not (POST_ID.fullmatch(str(post)) and isinstance(row, dict)):
            continue
        bid, t = _broadcast_id(row.get("broadcast")), _second(row.get("t"))
        if bid and t is not None:
            out[str(post)] = {"id": bid, "t": t}
    return out


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


def _written(text: str) -> dict[str, int]:
    """Every moment of the recording written in `text`, as written, to
    its second in the transcript. A time whose minutes or seconds are 60
    or more is not a time, and neither is a time of day."""
    out: dict[str, int] = {}
    text = text or ""
    # Every copy of a time of day stays words: the extension finds times
    # by their characters, so "8:30" cannot be a button in one place and
    # the clock in another.
    day_times = {f.group(1) for f in TIME.finditer(text)
                 if _CLOCK_OF_DAY.match(text, f.end())}
    for found in TIME.finditer(text):
        token = found.group(1)
        if token in day_times or any(int(p) >= 60 for p in token.split(":")[1:]):
            continue
        out[token] = _seconds(token)
    return out


def from_x(seconds: int, clock: dict) -> int | None:
    """A second of the X broadcast as a second of the transcript: to_x
    the other way, or None where the two clocks are not known to agree."""
    if not clock:
        return None
    try:
        stretches = clock.get("sections") or [{"from": 0, "to": None, "offset": clock["offset"]}]
        for stretch in stretches:
            at = seconds - int(round(stretch["offset"]))
            ends = stretch.get("to")
            if at >= 0 and stretch["from"] <= at and (ends is None or at < ends):
                return at
    except (KeyError, TypeError, AttributeError):
        logger.warning("x posts: an unreadable clock %s", clock)
    return None


def times_in(text: str, clock: dict) -> dict[str, int]:
    """Every time written in `text`, to the X second it plays at.

    Keyed by the time exactly as written, so the extension finds each one
    by the characters it sees and never parses a clock itself.
    """
    out: dict[str, int] = {}
    for token, seconds in _written(text).items():
        at = to_x(seconds, clock)
        if at is not None:
            out[token] = at
    return out


def clock_text(seconds: int) -> str:
    """1:39:53, the way the summaries write a time."""
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def watch(episode_id: str, seconds: int) -> str | None:
    """The whole show at this second, by the site's own link for it, or
    None when the archive has no address for the show or the address is
    not one of the three shapes LINK allows."""
    row = _summaries().get(episode_id) or {}
    url = podcast._deep_link(str(row.get("url") or ""), str(row.get("platform") or ""),
                             seconds, episode_id)
    return url if LINK.fullmatch(url) else None


def marks(text: str, row: dict) -> tuple[dict[str, int], dict[str, str]]:
    """(times that move the post's own player, times that open the whole
    show), for every time written in `text`.

    A recording has only the first kind. A clip has both: a time inside
    it plays in the clip, and every time, inside or not, can open the
    whole show there.
    """
    if row.get("kind") == "link":
        # The post's own video is the broadcast card, which X does not
        # let a link start at a second; every time opens the broadcast.
        links = {}
        for token, seconds in _written(text).items():
            at = to_x(seconds, row["clock"])
            link = f"{row['link']['broadcast']}?t={at}" if at is not None else ""
            if SEEKS.fullmatch(link):
                links[token] = link
        return {}, links
    if row.get("kind") != "clip":
        return times_in(text, row["clock"]), {}
    clip = row["clip"]
    times: dict[str, int] = {}
    links: dict[str, str] = {}
    for token, seconds in _written(text).items():
        if clip["start"] <= seconds <= clip["end"] + CLIP_SLACK_SECONDS:
            times[token] = seconds - clip["start"]
        link = watch(row["episode_id"], seconds)
        if link and SEEKS.fullmatch(link):
            links[token] = link
    return times, links


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
    if archive == "mcg":
        text = _mcg_markdown(episode_id)
    elif archive == "threadguy":
        text = _notes_markdown(_threadguy_summaries(), episode_id)
    times, links = marks(text or "", row)
    out = {
        "post": post,
        "kind": row.get("kind", "recording"),
        "archive": archive,
        "archive_name": ARCHIVE_NAMES[archive],
        "episode_id": episode_id,
        "title": (summary or {}).get("title") or row.get("title") or "",
        "show": (summary or {}).get("show") or None,
        "published_at": ((summary or {}).get("published_at")
                         or row.get("published_at") or None),
        "summary": text,
        "times": times,
        "links": links,
        "seekable": bool(row["clock"]) or row.get("kind") in ("clip", "link"),
        "page": page_for(archive, episode_id),
    }
    link = row.get("link")
    if link:
        watch_at = f"{link['broadcast']}?t={link['t']}"
        out["link"] = {"at": clock_text(link["at"]),
                       "watch": watch_at if SEEKS.fullmatch(watch_at) else None}
    clip = row.get("clip")
    if clip:
        after_at = clip["after_at"]
        # The full recording either way; a time only where it seeks.
        shown = watch(episode_id, clip["start"])
        after = watch(episode_id, after_at) if after_at is not None else None
        out["clip"] = {
            "at": clock_text(clip["start"]),
            "until": clock_text(clip["end"]),
            "watch": shown,
            "seeks": bool(shown and SEEKS.fullmatch(shown)),
            "before": clip["before"],
            "after": clip["after"],
            "after_at": clock_text(after_at) if after_at is not None else None,
            "after_watch": after if after and SEEKS.fullmatch(after) else None,
        }
    return out

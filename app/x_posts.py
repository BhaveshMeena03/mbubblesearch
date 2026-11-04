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
of Market Bubble or of his own videos several times a day, and MCG Live
clips its own show about ten times. Each one placed in the archive by its
own words (scripts/place_x_clips.py, written to data/x_clips.json) gets a
panel saying where it is from and what was said right after the cut. A
time inside the clip moves the clip's player; any other opens the whole
show at that second, on X where it aired there. And link posts: ours,
which link a show at the second rather than upload a clip, its X
broadcast since October and its YouTube copy before (scripts/place_x_links.py
reads the second off each link, written to data/x_links.json). The panel
names the moment and the show, and every time in it opens the show
there: on X where it aired, else on YouTube.

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

from . import mcg_on_x, names, podcast, youtube_map

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
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

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
# MCG Live and ThreadGuy, on 3 Nov 2026: MCG's profile is mostly clips of
# its own show, about ten a day, and none had a button.
CLIP_ACCOUNTS = frozenset({"blknoiz06", "marketbubble", "mbubblesearch", "kaiz_294",
                           "mcglive", "notthreadguy",
                           "orderbookshow", "bullclips", "insentosprime"})

# The archives a clip can be placed in. Market Bubble's episodes ship with
# their lines; MCG Live's and ThreadGuy's are read back per show by the
# script that places clips.
CLIP_ARCHIVES = ("podcast", "mcg", "threadguy")


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
        if (row.get("archive") not in CLIP_ARCHIVES or not clip_account(row.get("account"))
                or not EPISODE_ID.fullmatch(str(row.get("episode_id", "")))
                or start is None or end is None or end < start
                or (after_at is not None and _second(after_at) is None)
                or not isinstance(row.get("before", ""), str)
                or not isinstance(row.get("after", ""), str)
                or not isinstance(row.get("stitched", False), bool)):
            logger.warning("x posts: clip %s is malformed, left out", post)
            continue
        # The transcript's own words, so its misheard names too: the same
        # corrections the answers get (app/names.py).
        out[str(post)] = {"archive": row["archive"],
                          "episode_id": row["episode_id"], "start": start, "end": end,
                          "before": names.fix(row.get("before", ""))[0][:1500],
                          "after": names.fix(row.get("after", ""))[0][:3000],
                          "after_at": after_at,
                          # Cut together from several parts of the show, so
                          # its own clock is not the show's.
                          "stitched": row.get("stitched", False)}
    return out


@lru_cache(maxsize=1)
def shelf() -> dict[str, dict]:
    """Post id to what it is: archive, episode, and the clock to X."""
    posts: dict[str, dict] = {}
    # Each show the archive holds from YouTube, by its id: which archive,
    # and its row for the title.
    held = {str(r.get("id")): (archive, r)
            for archive, path in (("mcg", MCG_SHELF), ("rasmr", RASMR_SHELF),
                                  ("threadguy", THREADGUY_SHELF))
            for r in _rows(path)}
    titled = {video: row for video, (_, row) in held.items()}

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
    shows = _broadcasts()
    on_x = {show["episode_id"]: show for show in shows.values()}
    for post, clip in _clips().items():
        if post in posts:
            continue
        episode_id = clip["episode_id"]
        row = {"archive": clip["archive"], "episode_id": episode_id,
               "kind": "clip", "clock": {}, "clip": clip}
        if clip["archive"] != "podcast":
            # Indexed from YouTube, so the clip's seconds are YouTube's;
            # the whole show opens on X through the pair where it aired.
            if held.get(episode_id, ("",))[0] != clip["archive"]:
                continue
            paired = on_x.get(episode_id, {})
            row["full"] = {"broadcast": paired.get("url"), "youtube": episode_id,
                           "clock": paired.get("clock", {})}
            row["title"] = held[episode_id][1].get("title")
            row["published_at"] = held[episode_id][1].get("published_at")
        posts[post] = row

    # Link posts, last: a show at a second, on X or on YouTube, that maps
    # onto a transcript this archive holds. A link to a show we don't hold,
    # or into minutes the transcript doesn't have, gets no button.
    for post, link in _links().items():
        row = None if post in posts else _link_row(link, shows, on_x, held)
        if row:
            posts[post] = row
    return posts


def _link_row(link: dict, shows: dict[str, dict], on_x: dict[str, dict],
              held: dict[str, tuple[str, dict]]) -> dict | None:
    """A link post's shelf row, or None when it points at nothing held.

    A broadcast link is placed by the broadcast's own clock. A YouTube link
    is placed on the copy the archive was indexed from: a Market Bubble
    upload is cut from the X broadcast, so its second is moved back onto
    the broadcast's (app/youtube_map.py); an MCG, ThreadGuy or rasmr show
    was indexed from YouTube, so its second is already the transcript's.
    Elon and The Record are left out until they are wanted.
    """
    if "id" in link:
        show = shows.get(link["id"])
        at = from_x(link["t"], show["clock"]) if show else None
        if at is None:
            return None
        watch = f"{show['url']}?t={link['t']}"
    else:
        show, at = _youtube_show(link["youtube"], link["t"], on_x, held)
        if show is None:
            return None
        watch = None
    episode_id = show["episode_id"]
    shown = held.get(episode_id, ("", {}))[1]
    row = {"archive": show["archive"], "episode_id": episode_id, "kind": "link",
           "clock": show["clock"], "title": shown.get("title"),
           "published_at": shown.get("published_at"),
           "link": {"broadcast": show.get("url"), "youtube": show.get("youtube"), "at": at}}
    row["link"]["watch"] = watch or _moment(row, at)
    return row if row["link"]["watch"] and SEEKS.fullmatch(row["link"]["watch"]) else None


def _youtube_show(video: str, t: int, on_x: dict[str, dict],
                  held: dict[str, tuple[str, dict]]) -> tuple[dict | None, int]:
    """The show a YouTube link points into and the second of its transcript.
    X wins where the show aired there: the same moment opens on X."""
    live = youtube_map.live_moment(video, t)
    if live and _post_of(live[0]) and (live[0] in _summaries() or live[0] in on_x):
        episode_id, at = live
        return {"archive": "podcast", "episode_id": episode_id, "clock": {"offset": 0},
                "url": on_x.get(episode_id, {}).get("url")}, at
    if video in held:
        paired = on_x.get(video, {})
        return {"archive": held[video][0], "episode_id": video, "youtube": video,
                "clock": paired.get("clock", {}), "url": paired.get("url")}, t
    if (_summaries().get(video) or {}).get("platform") == "youtube":
        return {"archive": "podcast", "episode_id": video, "youtube": video,
                "clock": {}, "url": None}, t
    return None, t


def _moment(row: dict, seconds: int) -> str | None:
    """The whole show at a second of its transcript: on X where it aired
    there and the two clocks agree, else on YouTube, at the same words.

    YouTube is the stream's own copy for MCG, ThreadGuy and rasmr, so the
    transcript's second is YouTube's; for a Market Bubble episode indexed
    from X it is the upload, through the map. A moment X's stream missed,
    said before it began or in a gap where the clocks moved, still opens.
    """
    return _show_at(row["link"], row["clock"], row["episode_id"], seconds)


def _show_at(link: dict, clock: dict, episode_id: str, seconds: int) -> str | None:
    at = to_x(seconds, clock) if link.get("broadcast") else None
    if at is not None:
        url = f"{link['broadcast']}?t={at}"
    elif link.get("youtube"):
        url = f"https://www.youtube.com/watch?v={link['youtube']}&t={seconds}s"
    else:
        found = youtube_map.youtube_moment(episode_id, seconds)
        url = f"https://www.youtube.com/watch?v={found[0]}&t={found[1]}s" if found else ""
    return url if SEEKS.fullmatch(url) else None


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
                # The pair is keyed by the YouTube copy, the transcript's own
                # clock, so a moment X's stream missed can still open there.
                out[bid] = {"archive": archive, "episode_id": video, "clock": clock,
                            "url": f"https://x.com/i/broadcasts/{bid}",
                            "youtube": video if YOUTUBE_ID.fullmatch(video) else None}
    return out


@lru_cache(maxsize=1)
def _links() -> dict[str, dict]:
    """data/x_links.json, each row checked: post id to the broadcast or
    YouTube video it links and the second it links to."""
    out: dict[str, dict] = {}
    given = _json(LINKS) or {}
    for post, row in (given.items() if isinstance(given, dict) else ()):
        if not (POST_ID.fullmatch(str(post)) and isinstance(row, dict)):
            continue
        t = _second(row.get("t"))
        bid, video = _broadcast_id(row.get("broadcast")), str(row.get("youtube") or "")
        if t is None:
            continue
        if bid:
            out[str(post)] = {"id": bid, "t": t}
        elif YOUTUBE_ID.fullmatch(video):
            out[str(post)] = {"youtube": video, "t": t}
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


def _full_show(row: dict, seconds: int) -> str | None:
    """A clip's whole show at a second: Market Bubble's by the site's own
    link, an MCG Live or ThreadGuy show by its pair."""
    full = row.get("full")
    if full is None:
        return watch(row["episode_id"], seconds)
    return _show_at(full, full["clock"], row["episode_id"], seconds)


def marks(text: str, row: dict) -> tuple[dict[str, int], dict[str, str]]:
    """(times that move the post's own player, times that open the whole
    show), for every time written in `text`.

    A recording has only the first kind. A clip has both: a time inside
    it plays in the clip, and every time, inside or not, can open the
    whole show there.
    """
    if row.get("kind") == "link":
        # The post's own video is a card, which X does not let a link start
        # at a second; every time opens the show.
        links = {}
        for token, seconds in _written(text).items():
            link = _moment(row, seconds)
            if link:
                links[token] = link
        return {}, links
    if row.get("kind") != "clip":
        return times_in(text, row["clock"]), {}
    clip = row["clip"]
    times: dict[str, int] = {}
    links: dict[str, str] = {}
    for token, seconds in _written(text).items():
        if (not clip["stitched"]
                and clip["start"] <= seconds <= clip["end"] + CLIP_SLACK_SECONDS):
            times[token] = seconds - clip["start"]
        link = _full_show(row, seconds)
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
        out["link"] = {"at": clock_text(link["at"]), "watch": link["watch"]}
    clip = row.get("clip")
    if clip:
        after_at = clip["after_at"]
        # The full recording either way; a time only where it seeks.
        shown = _full_show(row, clip["start"])
        after = _full_show(row, after_at) if after_at is not None else None
        out["clip"] = {
            "at": clock_text(clip["start"]),
            # A stitched upload spans more of the show than it holds.
            "until": "" if clip["stitched"] else clock_text(clip["end"]),
            "watch": shown,
            "seeks": bool(shown and SEEKS.fullmatch(shown)),
            "before": clip["before"],
            "after": clip["after"],
            "after_at": clock_text(after_at) if after_at is not None else None,
            "after_watch": after if after and SEEKS.fullmatch(after) else None,
        }
    return out

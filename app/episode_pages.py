"""A page for each episode, written on the server.

Every answer on this site is made when somebody asks. Until then the
hours behind it are nowhere a search engine can read, which is why on 20
November 2026 Google held ten pages of this site and sent nobody to any
of them. An episode's summary already exists, written when the episode
was indexed, so each one is given an address of its own, /episodes/<id>,
and sent whole, with no script.

Only an episode with a summary gets a page. A title and a date is not a
page, it is a row.

What a page says is the summary on file and nothing else. The one
judgment made here is about its times. A Market Bubble summary's times
are the broadcast's own. An MCG or ThreadGuy summary's times are linked
only when it is marked "pinned": written since 28 October 2026, with
every topic checked against a line of the show. Older ones ran up to 35
minutes late, and a link that opens the wrong minute is worse than a
topic with no link, so those are listed as plain text.

A link at a second goes where the show plays at a second: the X
broadcast when the show went out on X and the clocks are known to agree
there, otherwise the YouTube copy. One of Ansem's own videos on X does
not open at a second at all, so its times are text.

Pure: summaries, listings and pair files in, text out. The pages are
written here so a test can read exactly what a crawler is sent.
"""

from __future__ import annotations

import html
import re

from . import mcg_on_x
from .guests import fill, long_date
from .schemas import his_own
from .seo import SITE

# What an archive is called on a page, where it is searched, and its colour.
ARCHIVES = {
    "podcast": {"name": "Market Bubble", "search": "/demo/podcast.html",
                "accent": "#16c784", "image": "og-broadcast.png?v=2"},
    "mcg": {"name": "MCG Live", "search": "/demo/mcg.html",
            "accent": "#6ecf9e", "image": "og-mcg.png?v=2"},
    "threadguy": {"name": "ThreadGuy", "search": "/demo/threadguy.html",
                  "accent": "#6aa9ff", "image": "og-threadguy.png"},
}

_ID = re.compile(r"x-\d{15,20}|[A-Za-z0-9_-]{11}")
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# The only places a page sends anybody: the show's player on X at a
# second, the YouTube copy at a second, or the recording itself.
_SEEKS = re.compile(r"https://x\.com/i/broadcasts/[A-Za-z0-9]{6,20}\?t=\d{1,6}"
                    r"|https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&t=\d{1,6}s")
_RECORDING = re.compile(r"https://(?:x\.com|www\.youtube\.com|youtu\.be)/[\w\-./?=&%@]+")
_STAMP = r"\d{1,2}:\d{2}(?::\d{2})?"
# A topic's time, as the summaries write it: [0:04:31], **[0:04:31]**,
# [2:30:00–2:45:00], **[0:10:00]–[0:14:00]**.
_LEAD = re.compile(
    rf"\*{{0,2}}\[({_STAMP})(?:\s*[–—-]\s*({_STAMP}))?\]\*{{0,2}}"
    rf"(?:\s*[–—-]\s*\*{{0,2}}\[({_STAMP})\]\*{{0,2}})?\s*[:–—-]?\s*")
_TLDR = re.compile(r"\*\*TL;DR:?\*\*:?\s*(?:[–—-]\s*)?(.*)", re.I)
_HEADING = re.compile(r"\*\*([^*]{2,60}?):?\*\*:?")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ASIDE = re.compile(r"\*[^*].*[^*]\*")
_DAY = 24 * 3600


def is_id(value: object) -> bool:
    """An episode's id as it may appear in an address: a YouTube id or a
    broadcast's, nothing a path or a tag could be built out of."""
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def clock(seconds: object) -> str:
    """4520 as '1:15:20', 320 as '5:20'."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _second(value: object) -> int | None:
    """A time a page may print or link, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if 0 <= value < _DAY else None


def _stamp(token: str) -> int | None:
    parts = [int(p) for p in token.split(":")]
    if len(parts) == 2:
        parts.insert(0, 0)
    hours, minutes, secs = parts
    if minutes > 59 or secs > 59:
        return None
    return _second(hours * 3600 + minutes * 60 + secs)


def length(seconds: object) -> str:
    """How long a recording runs, in words: '3 hr 12 min', '41 min'."""
    total = _second(seconds)
    if not total or total < 60:
        return ""
    hours, minutes = divmod(round(total / 60), 60)
    return f"{hours} hr {minutes:02d} min" if hours else f"{minutes} min"


# --- where a moment plays ----------------------------------------------------------

def moment(episode_id: str, seconds: int, *, player: str = "",
           pair: dict | None = None) -> str:
    """The link that opens a show at a second, or "" when there is none.

    `player` is a Market Bubble broadcast's own player on X, whose clock
    is the summary's. `pair` is the X broadcast of a show indexed from
    YouTube and how far apart the two clocks are (app/mcg_on_x.py).
    """
    at = _second(seconds)
    if at is None:
        return ""
    link = ""
    if player:
        link = f"{player}?t={at}"
    else:
        if isinstance(pair, dict) and pair.get("broadcast"):
            gap = mcg_on_x._offset(pair, at)
            if gap is not None and at + gap >= 0:
                link = f"{pair['broadcast']}?t={at + gap}"
        if not link and _YOUTUBE_ID.fullmatch(episode_id):
            link = f"https://www.youtube.com/watch?v={episode_id}&t={at}s"
    return link if _SEEKS.fullmatch(link) else ""


def recording(link: object) -> str:
    """The address of a recording, if it is one on X or YouTube."""
    link = str(link or "")
    return link if _RECORDING.fullmatch(link) else ""


# --- reading a summary -------------------------------------------------------------

def read_summary(text: str) -> tuple[str, list[dict]]:
    """A Market Bubble summary as its TL;DR and the blocks under it.

    The summaries are markdown a model wrote, and no two of them are laid
    out quite alike, so this takes what it finds: a heading, a bullet
    with or without a time in front, a paragraph. A line it does not
    know is kept as a paragraph. The one thing left out is the model's
    own aside in italics ("*This summary is not financial advice.*"):
    the page says that once, in its own words.
    """
    tldr: list[str] = []
    blocks: list[dict] = []
    in_tldr = False
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or set(line) <= {"-", "*", "_"}:
            continue                    # blank, the title again, or a rule
        if _ASIDE.fullmatch(line):
            continue
        opened = _TLDR.fullmatch(line)
        if opened:
            in_tldr = True
            if opened.group(1):
                tldr.append(opened.group(1))
            continue
        heading = _HEADING.fullmatch(line)
        if heading:
            in_tldr = False
            blocks.append({"kind": "h", "text": heading.group(1).strip()})
            continue
        if line[:2] in ("- ", "* ", "• "):
            in_tldr = False
            rest = line[2:].strip()
            at = end = None
            lead = _LEAD.match(rest)
            if lead:
                at = _stamp(lead.group(1))
                until = lead.group(2) or lead.group(3)
                end = _stamp(until) if until else None
                rest = rest[lead.end():].strip()
            if rest:
                blocks.append({"kind": "li", "t": at, "to": end, "text": rest})
            continue
        if in_tldr:
            tldr.append(line)
        else:
            blocks.append({"kind": "p", "text": line})
    return " ".join(tldr).strip(), blocks


def _shown(text: object) -> str:
    """Summary text on its way into a page: escaped, then its bold marks
    turned into bold. Escaping first, so nothing in a summary is markup."""
    safe = html.escape(" ".join(str(text or "").split()), quote=True)
    return _BOLD.sub(lambda found: f"<b>{found.group(1)}</b>", safe).replace("**", "")


def _plain(text: object) -> str:
    """The same text with its marks taken out, for a tag or a card."""
    return " ".join(str(text or "").replace("**", "").split())


# --- one episode, whichever archive it is in ----------------------------------------

def from_summary(row: dict, *, player: str = "", calls: list[dict] | None = None) -> dict | None:
    """A Market Bubble row, or one of Ansem's appearances elsewhere, as a
    page. None when there is nothing to say on one."""
    ident = row.get("episode_id")
    if not is_id(ident) or not str(row.get("title") or "").strip():
        return None
    tldr, blocks = read_summary(row.get("summary") or "")
    if not tldr and not blocks:
        return None
    show = str(row.get("show") or "")
    own = his_own(show)
    for block in blocks:
        at = block.get("t")
        block["link"] = ("" if own or at is None else
                         moment(ident, at, player=player if ident.startswith("x-") else ""))
    return {"archive": "podcast", "id": ident, "title": _plain(row["title"]),
            "show": show or ARCHIVES["podcast"]["name"],
            "date": str(row.get("published_at") or "")[:10],
            "seconds": None, "watch": recording(row.get("url")),
            "tldr": tldr, "blocks": blocks,
            "calls": [c for c in calls or [] if c.get("episode_id") == ident]}


def from_note(archive: str, row: dict, note: dict | None, pair: dict | None = None) -> dict | None:
    """An MCG or ThreadGuy recording and its note, as a page. None when
    the recording has no note."""
    ident = row.get("id")
    if (archive not in ARCHIVES or not is_id(ident) or not isinstance(note, dict)
            or not str(row.get("title") or "").strip()
            or not isinstance(note.get("tldr"), str) or not note["tldr"].strip()):
        return None
    pinned = note.get("pinned") is True
    blocks: list[dict] = []
    for topic in note.get("topics") or []:
        if not isinstance(topic, dict) or not str(topic.get("text") or "").strip():
            continue
        at = _second(topic.get("t")) if pinned else None
        blocks.append({"kind": "li", "t": at, "to": None, "text": topic["text"],
                       "link": moment(ident, at, pair=pair) if at is not None else ""})
    if blocks:
        blocks.insert(0, {"kind": "h", "text": "Topics"})
    return {"archive": archive, "id": ident, "title": _plain(row["title"]),
            "show": ARCHIVES[archive]["name"],
            "date": str(row.get("published_at") or "")[:10],
            "seconds": _second(row.get("seconds")), "watch": recording(row.get("url")),
            "tldr": note["tldr"].strip(), "blocks": blocks, "calls": []}


def in_order(episodes: list[dict]) -> list[dict]:
    """Newest first, and the same order every time."""
    return sorted(episodes, key=lambda e: (e["date"], e["id"]), reverse=True)


def neighbours(episodes: list[dict], ident: str) -> tuple[dict | None, dict | None]:
    """The recording after this one in its archive, and the one before."""
    here = next((e for e in episodes if e["id"] == ident), None)
    if here is None:
        return None, None
    shelf = in_order([e for e in episodes if e["archive"] == here["archive"]])
    at = next(i for i, e in enumerate(shelf) if e["id"] == ident)
    return (shelf[at - 1] if at else None,
            shelf[at + 1] if at + 1 < len(shelf) else None)


def address(ident: str) -> str:
    return f"{SITE}/episodes/{ident}"


# --- the pages ---------------------------------------------------------------------

def _e(value: object) -> str:
    return html.escape(" ".join(str(value if value is not None else "").split()),
                       quote=True)


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, ending on a whole word."""
    text = _plain(text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _block_html(block: dict) -> str:
    if block["kind"] == "h":
        return f'<h2>{_shown(block["text"])}</h2>'
    if block["kind"] == "p":
        return f'<p class="para">{_shown(block["text"])}</p>'
    at, link = block.get("t"), block.get("link") or ""
    label = "" if at is None else clock(at)
    if label and block.get("to") is not None:
        label += f" to {clock(block['to'])}"
    if link:
        time = (f'<a class="at" href="{_e(link)}" target="_blank" rel="noopener" '
                f'aria-label="Watch at {_e(clock(at))}">{_e(label)}</a>')
    else:
        time = f'<span class="at off">{_e(label)}</span>' if label else '<span class="at"></span>'
    return f'<div class="topic">{time}<p>{_shown(block["text"])}</p></div>'


def _call_html(call: dict) -> str:
    link = str(call.get("link") or "")
    stamp = _e(call.get("timestamp"))
    watch = (f'<a class="at" href="{_e(link)}" target="_blank" rel="noopener">{stamp}</a>'
             if _SEEKS.fullmatch(link) else f'<span class="at off">{stamp}</span>')
    return (f'<div class="topic">{watch}<p><b>{_e(call.get("speaker"))} on '
            f'{_e(call.get("asset"))}:</b> “{_e(call.get("quote"))}”</p></div>')


def _step(episode: dict | None, label: str) -> str:
    if episode is None:
        return "<span></span>"
    return (f'<a class="step" href="/episodes/{_e(episode["id"])}">'
            f'<span>{label}</span><b>{_e(episode["title"])}</b></a>')


def render_page(template: str, episode: dict, newer: dict | None = None,
                older: dict | None = None) -> str:
    """One episode's page."""
    archive = ARCHIVES[episode["archive"]]
    title, show = episode["title"], episode["show"]
    when, runs = long_date(episode["date"]), length(episode["seconds"])
    said = episode["tldr"] or f"What was covered on {title}."
    described = _cut(said, 220)
    plate = " · ".join([f"<b>{_e(show)}</b>"] + [_e(part) for part in (when, runs) if part])
    watch = (f'<a class="btn btn-ghost" href="{_e(episode["watch"])}" target="_blank" '
             f'rel="noopener">Watch the recording</a>' if episode["watch"] else "")
    calls = ""
    if episode["calls"]:
        calls = ('<h2>Calls made on this show</h2>\n'
                 + "\n".join(_call_html(c) for c in episode["calls"])
                 + '\n<p class="more"><a href="/calls">What each call has done since</a></p>')
    timed = any(b.get("link") for b in episode["blocks"])
    return fill(template, {
        "title": _e(f"{_cut(title, 90)} · summary | Market Bubble Search"),
        "description": _e(described),
        "og_title": _e(_cut(title, 110)),
        "url": _e(address(episode["id"])),
        "image": _e(f"{SITE}/demo/{archive['image']}"),
        "accent": archive["accent"],
        "plate": plate,
        # No time to show on any line: the margin column is dropped.
        "mode": "" if any(b.get("t") is not None for b in episode["blocks"])
                or episode["calls"] else " plain",
        "name": _e(title),
        "tldr": _shown(episode["tldr"]),
        "watch": watch,
        "search": archive["search"],
        "archive": _e(archive["name"]),
        "body": "\n".join(_block_html(b) for b in episode["blocks"]),
        "calls": calls,
        "newer": _step(newer, "Newer"),
        "older": _step(older, "Older"),
        "times": ("Each time opens the show at that second. " if timed else ""),
    })


def render_index(template: str, episodes: list[dict]) -> str:
    """Every episode that has a page, by archive, newest first."""
    sections = []
    for key, archive in ARCHIVES.items():
        shelf = in_order([e for e in episodes if e["archive"] == key])
        if not shelf:
            continue
        rows = "\n".join(
            f'<a class="row" href="/episodes/{_e(e["id"])}"><span class="day">'
            f'{_e(long_date(e["date"]))}</span><b>{_e(e["title"])}</b></a>'
            for e in shelf)
        sections.append(
            f'<section style="--green:{archive["accent"]}">'
            f'<h2>{_e(archive["name"])} <span>{len(shelf)}</span></h2>\n{rows}\n</section>')
    return fill(template, {"sections": "\n".join(sections), "count": str(len(episodes))})


def render_missing(template: str, count: int) -> str:
    """What an address with no episode behind it is sent: the way to the
    list, and not the list. The list is a hundred kilobytes, and an
    address anybody can make up should not be able to ask for it."""
    return fill(template, {"count": str(count), "sections": (
        '<p class="lead">There is no episode at this address. '
        '<a href="/episodes">See every episode</a>.</p>')})

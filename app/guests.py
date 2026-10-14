"""What a guest said on the show, under that guest's name.

The hosts are in every episode, so their voices could be named by
recurrence. A guest appears once. Naming one took three things that had
to agree: the show's own banner, the name under the guest's tile, and a
frame in which that person is visibly the one talking. The voice of the
line being spoken in that frame is then the guest's voice, and only lines
in that voice can be quoted under the name.

It is stricter than the banner on purpose. The banner was read by
machine, and it was wrong where it mattered: one window it called BREZ
shows a tile labelled RAJ under a banner reading LIVE WITH MERT, and it
gave one guest the previous guest's job title. So nobody is on a guest
page until a person has looked at them (data/guest_roster.json), and a
window with a second large unidentified voice in it contributes only the
one that was seen.

Everything here is pure: it reads the transcript, the list of lines in a
guest's voice (data/guest_voices.json) and the hosts' labels. That is what
lets a test hold every shipped quote against all three. The pages are
written here too, as text, so the same tests can read exactly what a
visitor and a crawler are sent.
"""

from __future__ import annotations

import html
import re
from datetime import date

from . import calls

# A quote on a guest's page is something worth reading on its own.
MIN_WORDS, MAX_WORDS = 10, 60
# How much of a quote may sit on lines whose voice was too short to
# fingerprint. The first and last line are always the guest's.
MAX_UNKNOWN = 0.34

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# The only place a quote's link may go: the show's own player on X, at a
# second. Anything else in the data is not somewhere a visitor is sent.
_PLAYER = re.compile(r"https://x\.com/i/broadcasts/[A-Za-z0-9]{6,20}\?t=\d{1,6}")
_FIELD = re.compile(r"\{\{([a-z_]+)\}\}")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
# Written into every page's address and card. Not taken from the request:
# a Host header is whatever the sender says it is.
SITE = "https://search.lexthedev.com"


def is_slug(value: object) -> bool:
    """A guest's address: lower case words joined by hyphens, nothing a
    path or a tag could be built out of."""
    return isinstance(value, str) and len(value) <= 40 and bool(_SLUG.fullmatch(value))


def in_voice(voice: set[int], hosts: dict[str, str], first: int, last: int) -> bool:
    """True when a run of lines can be put under a guest's name.

    It has to open and close in the guest's voice, no line in it may be
    a host's, and at most a third of it may be lines too short to have a
    voice at all ("yeah", "right") which sit inside the sentence.
    """
    if first not in voice or last not in voice:
        return False
    span = range(first, last + 1)
    if any(hosts.get(str(i)) for i in span):
        return False
    unknown = sum(1 for i in span if i not in voice)
    return unknown <= MAX_UNKNOWN * len(span)


def accept(raw: dict, episode: dict, voice: set[int],
           hosts: dict[str, str]) -> tuple[dict | None, str]:
    """A proposed quote, held against the transcript and the voice.

    Returns the quote as the page will show it, cut from the transcript,
    or None and the reason it was refused.
    """
    segments = episode.get("segments") or []
    try:
        near = int(raw.get("line"))
    except (TypeError, ValueError):
        return None, "no line"
    said = str(raw.get("quote") or "")
    count = len(calls.words(said))
    if not MIN_WORDS <= count <= MAX_WORDS:
        return None, "too short or too long"
    span = calls.locate(said, segments, near)
    if span is None:
        return None, "quote is not in the transcript"
    if not in_voice(voice, hosts, span.first, span.last):
        return None, "not in the guest's voice"
    return {
        "id": f"{episode['episode_id']}:{span.first}",
        "line": span.first, "last_line": span.last,
        "start_seconds": float(segments[span.first].get("t", 0.0)),
        "quote": calls.cut(segments, span),
        "about": " ".join(str(raw.get("about") or "").split())[:80],
    }, ""


def apart(found: list[dict], gap: int = 4) -> list[dict]:
    """One quote per moment: two that overlap or sit within a few lines
    of each other are the same thing said once."""
    kept: list[dict] = []
    for quote in sorted(found, key=lambda q: q["line"]):
        if kept and quote["line"] - kept[-1]["last_line"] <= gap:
            continue
        kept.append(quote)
    return kept


def on_air(voice: set[int], segments: list[dict]) -> tuple[float, float] | None:
    """From the guest's first line to the last, in seconds."""
    inside = sorted(i for i in voice if 0 <= i < len(segments))
    if not inside:
        return None
    return (float(segments[inside[0]].get("t", 0.0)),
            float(segments[inside[-1]].get("t", 0.0)))


# --- as served -------------------------------------------------------------------

def lead(guest: dict) -> dict:
    """The quote a guest is introduced by: the one a person marked, or
    the first one said."""
    quotes = guest.get("quotes") or []
    return next((q for q in quotes if q.get("lead")), quotes[0] if quotes else {})


def index(shipped: dict) -> dict:
    """Every guest with a page, newest appearance first. Two on one show
    are in the order they came on, the later one first."""
    latest = sorted(shipped.get("guests") or [],
                    key=lambda g: (g["date"], float(g.get("on_from") or 0.0)),
                    reverse=True)
    rows = [{"slug": g["slug"], "name": g["name"], "role": g.get("role") or "",
             "show": g["show"], "date": g["date"],
             "quotes": len(g.get("quotes") or []),
             "first": lead(g).get("quote", "")}
            for g in latest]
    return {"guests": rows, "built_at": shipped.get("built_at")}


def page(shipped: dict, slug: str) -> dict | None:
    """One guest as the page shows them, or None."""
    if not is_slug(slug):
        return None
    for guest in shipped.get("guests") or []:
        if guest.get("slug") == slug:
            return {key: guest.get(key) for key in
                    ("slug", "name", "also", "role", "show", "date", "episode_id",
                     "on_from", "on_to", "from_stamp", "to_stamp", "link",
                     "lines", "quotes")}
    return None


# --- as written ------------------------------------------------------------------
#
# Both pages go out whole, with no script to run. X's crawler does not run
# one, so a page filled in by the browser would share as a card with no
# name on it, and a search engine would find a page with nobody on it.

def _e(value: object) -> str:
    """Anything on its way into a page. It lands in text and in
    attributes, so the quotes are escaped as well."""
    return html.escape(" ".join(str(value if value is not None else "").split()),
                       quote=True)


def long_date(iso: object) -> str:
    """'2026-08-13' as 'August 13, 2026'. Empty for anything else."""
    text = str(iso)[:10]
    # Only the one shape. Which others fromisoformat takes depends on the
    # Python it runs under.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return ""
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return ""
    return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"


def player(link: object) -> str:
    """A link the page may send somebody to, or nothing."""
    link = str(link or "")
    return link if _PLAYER.fullmatch(link) else ""


def fill(template: str, values: dict[str, str]) -> str:
    """A page with its fields filled in.

    One pass over the template, so nothing a value contains is read as a
    field. A field the template names and nobody supplied is a mistake in
    this file, and it raises rather than going out as a pair of braces.
    """
    return _FIELD.sub(lambda found: values[found.group(1)], template)


def _quote_html(quote: dict) -> str:
    link = player(quote.get("link"))
    watch = (f'<a class="watch" href="{_e(link)}" target="_blank" rel="noopener">'
             f'Watch at {_e(quote.get("timestamp"))}</a>' if link else
             f'<span class="at">{_e(quote.get("timestamp"))}</span>')
    return ('<article class="said">'
            f'<p class="about">{_e(quote.get("about"))}</p>'
            f'<blockquote>{_e(quote.get("quote"))}</blockquote>'
            f'<p class="acts">{watch}</p>'
            '</article>')


def card_words(guest: dict) -> tuple[str, str, str, str]:
    """What goes on a guest's share card: name, role, day, and the line
    the guest is introduced by."""
    return (str(guest.get("name") or ""), str(guest.get("role") or ""),
            long_date(guest.get("date")), str(lead(guest).get("quote") or ""))


def render_page(template: str, guest: dict) -> str:
    """One guest's page."""
    name, role, when, opening = card_words(guest)
    address = f"{SITE}/guests/{guest['slug']}"
    who = f"{name}, {role}," if role else name
    described = (f"{who} on Market Bubble on {when}. Quoted word for word, "
                 "each line linked to the second it was said.")
    link = player(guest.get("link"))
    watch = (f'<a class="btn btn-ghost" href="{_e(link)}" target="_blank" rel="noopener">'
             f'Watch from {_e(guest.get("from_stamp"))}</a>' if link else "")
    return fill(template, {
        "title": _e(f"{name} on Market Bubble · quoted to the second"),
        "description": _e(described),
        "og_title": _e(f"{name} on Market Bubble"),
        "og_description": _e(f"\u201c{opening}\u201d" if opening else described),
        "url": _e(address),
        "image": _e(f"{SITE}/og/guest/{guest['slug']}.png"),
        "name": _e(name),
        "also": (f'<span class="also">also {_e(guest.get("also"))}</span>'
                 if guest.get("also") else ""),
        "role": f'<p class="role">{_e(role)}</p>' if role else "",
        "when": _e(when),
        "from": _e(guest.get("from_stamp")),
        "to": _e(guest.get("to_stamp")),
        "watch": watch,
        "quotes": "\n".join(_quote_html(q) for q in guest.get("quotes") or []),
    })


def render_index(template: str, shipped: dict) -> str:
    """The list of guests."""
    rows = []
    for guest in index(shipped)["guests"]:
        if not is_slug(guest["slug"]):
            continue
        role = f'<span class="role">{_e(guest["role"])}</span>' if guest["role"] else ""
        rows.append(
            f'<a class="guest" href="/guests/{guest["slug"]}">'
            f'<span class="who"><b>{_e(guest["name"])}</b>{role}</span>'
            f'<span class="line">{_e(guest["first"])}</span>'
            f'<span class="day">{_e(long_date(guest["date"]))}</span>'
            '</a>')
    return fill(template, {"guests": "\n".join(rows)})

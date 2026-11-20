"""What search engines are told about this site: robots.txt, sitemap.xml
and which pages may be indexed at all.

On 20 November 2026 Google had ten of these pages and ranked none of
them. There was no robots.txt and no sitemap, no page named its own
address, so /home and /demo/home.html were two pages to a crawler, and a
share-card template under /demo was as indexable as the front door.

The rule is an allowlist. A page under /demo is indexed only if it is
named here. Anything else, a card template or a page still being looked
at, is sent "X-Robots-Tag: noindex" without anybody having to remember
to mark it.

Nothing is blocked in robots.txt, on purpose. A page that keeps itself
out of search does it with a noindex tag, and a crawler that is not
allowed to fetch the page never reads the tag: the address can then be
indexed anyway, bare, from a link somewhere else.

Pure text in, pure text out. No network and no archive.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

SITE = "https://search.lexthedev.com"

# The pages of this site, in the order the sitemap lists them, each with
# the archive whose newest recording dates it. None when the page does not
# change with an archive, "*" for the newest of all of them.
PAGES: tuple[tuple[str, str | None], ...] = (
    ("podcast.html", "podcast"),
    ("home.html", "*"),
    ("mcg.html", "mcg"),
    ("mcg-assets.html", "mcg"),
    ("threadguy.html", "threadguy"),
    ("threadguy-assets.html", "threadguy"),
    ("rasmr.html", "rasmr"),
    ("elon.html", "elon"),
    ("finance.html", "tradfi"),
    ("assets.html", "podcast"),
    ("calls.html", None),
    ("how-it-works.html", None),
    ("extension-privacy.html", None),
)

# Public pages of the other tools this service also answers for. They
# may be indexed, each at the address that is its own, and are not in
# this site's sitemap.
ELSEWHERE = {
    "concierge.html": "https://concierge.lexthedev.com/demo/concierge.html",
    "clawpump.html": f"{SITE}/demo/clawpump.html",
}

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def canonical(name: str) -> str | None:
    """The one address a page under /demo is indexed at, or None when it
    is not indexed."""
    if any(name == page for page, _ in PAGES):
        return f"{SITE}/demo/{name}"
    return ELSEWHERE.get(name)


def robots_txt() -> str:
    return ("# Everything here may be read. A page that stays out of search\n"
            "# says so itself, which only works if it can be fetched.\n"
            "User-agent: *\n"
            "Disallow:\n"
            "\n"
            f"Sitemap: {SITE}/sitemap.xml\n")


def sitemap_xml(newest: dict[str, str] | None = None) -> str:
    """The sitemap. `newest` is archive -> the day of its newest
    recording; a page whose archive is missing from it, or whose date is
    not a date, is listed without one rather than with a guess."""
    days = {name: str(day)[:10] for name, day in (newest or {}).items()
            if _DAY.fullmatch(str(day)[:10])}
    latest = max(days.values(), default="")
    rows = []
    for name, archive in PAGES:
        day = latest if archive == "*" else days.get(archive or "", "")
        stamp = f"<lastmod>{day}</lastmod>" if day else ""
        rows.append(f"  <url><loc>{escape(canonical(name))}</loc>{stamp}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")


def robots_header(path: str) -> str | None:
    """"noindex" for an address that must not be in search, else None.

    Under /demo that is every page not named above. The API is left out
    of search too: its replies are data for the pages, and a crawler
    rendering a page may still fetch them, which noindex does not stop.
    """
    if path.startswith(("/v1/", "/x-bot/")) or path == "/healthz":
        return "noindex"
    if path in ("/demo", "/demo/"):
        return "noindex"                # the directory's own index.html
    if path.startswith("/demo/") and path.endswith(".html"):
        return None if canonical(path[len("/demo/"):]) else "noindex"
    return None

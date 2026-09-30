"""Every archive has a way back to the front door, in the same place.

The corners disagreed. MCG's and the Musk page's linked back to themselves,
the broadcast page's brand pointed at itself, the front door's own mark was
not a link at all, and The Record had nothing in the corner. With five
archives and a front door there was no way home from any of them except
the browser's back button.
"""

from __future__ import annotations

import re
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent / "demo"

# The pages people browse. The *-card.html files are share-card sources.
ARCHIVES = ["podcast", "mcg", "mcg-assets", "elon", "finance", "threadguy"]


def _page(name: str) -> str:
    return (DEMO / f"{name}.html").read_text()


def test_every_archive_links_home_from_its_corner():
    for name in ARCHIVES:
        page = _page(name)
        assert re.search(r'href="/home"', page), f"{name} has no way home"


def test_the_archives_keep_their_own_name_in_the_corner():
    """The parent sits in front of each room's name rather than replacing
    it: the corner is where each archive says what it is."""
    for name, own in (("mcg", "The MCG Index"), ("elon", "THE MUSK RECORD"),
                      ("threadguy", "ThreadGuy")):
        page = _page(name)
        assert 'class="home-crumb" href="/home"' in page, name
        # read the corner, not the <title>, which names the room first
        crumb = page.index('class="home-crumb"')
        corner = page[crumb:crumb + 600]
        assert own in corner, f"{name}: its own name should follow the crumb"


def test_the_crumb_is_styled_once_for_every_page():
    """In the shared nav rather than five copies, and in the monospace
    every page already uses: left to inherit, it fell through to the
    browser's default serif beside a bold sans."""
    nav = (DEMO / "nav.js").read_text()
    assert ".home-crumb{" in nav
    assert "IBM Plex Mono" in nav.split(".home-crumb{")[1].split("}")[0] \
        or "IBM Plex Mono" in nav.split(".home-crumb{")[1][:400]


def test_the_front_doors_own_mark_is_a_link():
    page = _page("home")
    assert '<a class="mark" href="/home">' in page


def test_the_broadcast_brand_goes_home_not_to_itself():
    assert '<a class="brand" href="/home">' in _page("podcast")

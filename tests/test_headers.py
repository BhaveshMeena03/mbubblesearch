"""One header on every channel page.

On 20 November 2026 the site's own name was 31px of serif on the front
door, 15px of sans on the Market Bubble page, and a line of 10px capitals
at 62% on the other five. MCG's corner was heavy capitals where
ThreadGuy's and rasmr's were the display serif, and its nav ended in a
"Search" that was not a link to anywhere.
"""

import re
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "demo"
NAV = (DEMO / "nav.js").read_text()
HOME = (DEMO / "home.html").read_text()
PODCAST = (DEMO / "podcast.html").read_text()

# Every page whose corner is the site's name, a slash, and its own.
CRUMBED = ["mcg", "mcg-assets", "threadguy", "threadguy-assets", "rasmr", "elon", "finance"]
NAMED = ["mcg", "mcg-assets", "threadguy", "threadguy-assets", "rasmr", "elon"]
LOCKUP = ('<a class="home-crumb" href="/home"><span class="w">Market Bubble</span>'
          '<span class="s">Search</span></a>')


def page(name: str) -> str:
    return (DEMO / f"{name}.html").read_text()


def rule(css: str, selector: str) -> str:
    """The body of the first rule for a selector, as written."""
    found = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert found, selector
    return " ".join(found.group(1).split())


def nav_rule(selector: str) -> str:
    """The same, out of the rules demo/nav.js writes into every page."""
    css = "".join(re.findall(r'^\s*"([^"\n]*)",?\s*$', NAV, re.M))
    return rule(css, selector)


@pytest.mark.parametrize("name", CRUMBED)
def test_every_channel_page_opens_with_the_sites_name_in_its_two_parts(name):
    assert page(name).count(LOCKUP) == 1


def test_the_name_is_the_size_the_front_door_sets_it():
    """One pair of sizes in three places. They are held to each other
    here so that changing one is a red test and not a mismatch nobody
    sees until a screenshot."""
    big, small = "font-size:31px", "font-size:12.5px"
    assert big in rule(HOME, ".mark .w") and small in rule(HOME, ".mark .s")
    assert big in nav_rule(".home-crumb .w") and small in nav_rule(".home-crumb")
    assert big in rule(PODCAST, ".brand-name .w") and small in rule(PODCAST, ".brand-name .s")
    for css in (rule(HOME, ".mark .w"), nav_rule(".home-crumb .w")):
        assert "Instrument Serif" in css
    assert "var(--display)" in rule(PODCAST, ".brand-name .w")


def test_the_market_bubble_page_carries_the_same_name_and_it_goes_home():
    assert ('<a class="brand" href="/home">' in PODCAST
            and '<span class="brand-name"><span class="w">Market Bubble</span>'
                '<span class="s">Search</span></span>' in PODCAST)
    assert "font-size:15px;font-weight:600;letter-spacing:-.01em" not in PODCAST


def test_on_a_phone_the_name_gives_way_to_the_switcher_beside_it():
    phone = NAV.split("@media (max-width:760px){", 1)[1]
    assert ".home-crumb .w{font-size:25px}" in phone
    assert ".navbar.crumbed.stuck .home-crumb .w{font-size:21px}" in phone
    assert "@media (max-width:420px){.home-crumb .s{display:none}}" in NAV
    assert "@media(max-width:560px){.mark{gap:9px}.mark .w{font-size:25px}" in HOME
    assert ".brand-name .w{font-size:25px}" in PODCAST


@pytest.mark.parametrize("name", NAMED)
def test_every_archive_sets_its_own_name_the_same_way(name):
    """The display serif at 22px, as ThreadGuy's and rasmr's were."""
    css = rule(page(name), ".mark b")
    assert "font-size:22px" in css, css
    assert "font-weight:800" not in css and "uppercase" not in css, css
    assert "Instrument Serif" in css or "var(--display)" in css, css


@pytest.mark.parametrize("name", CRUMBED + ["podcast", "home", "assets", "calls"])
def test_nothing_in_a_header_looks_like_a_link_and_goes_nowhere(name):
    html = page(name)
    # Three of these pages are written without a body tag.
    top = html[html.index('<div class="wrap">'):][:6000]
    dead = [a for a in re.findall(r"<a\b[^>]*>", top) if "href=" not in a]
    assert not dead, dead


def test_mcg_offers_its_tokens_the_way_threadguy_does():
    corner = re.split(r"<header[^>]*>", page("mcg"), maxsplit=1)[1].split("</header>", 1)[0]
    assert '<a class="tokens" href="/mcg/assets">Tokens</a>' in corner
    theirs = re.split(r"<header[^>]*>", page("threadguy"), maxsplit=1)[1].split("</header>", 1)[0]
    assert '<a class="tokens" href="/threadguy/tokens">Tokens</a>' in theirs
    assert "<span data-archives></span>" in corner

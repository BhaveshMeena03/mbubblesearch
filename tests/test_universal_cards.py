"""One share card per archive page, and nothing on any of them goes stale.

Four of the six cards were wrong when this replaced them: The Record's said
30 recordings with 35 live and no CZ, the Musk card 11 recordings to 2025
with 14 to 2026, the front door 1,832 hours with 1,967. An archive that
grows every morning cannot have a card redrawn every morning, so the cards
carry what a page is and one question it answers, never a count.
"""

import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "demo" / "og-universal-card.html").read_text()
SITE = "https://search.lexthedev.com"

# page file -> the card image it shares
PAGES = {"home.html": "og-home.png", "podcast.html": "og-broadcast.png",
         "mcg.html": "og-mcg.png", "elon.html": "og-musk.png",
         "finance.html": "og-finance.png", "threadguy.html": "og-threadguy.png"}


def entries():
    block = TEMPLATE.split("var PAGES = {")[1].split("\n  };")[0]
    return dict(re.findall(r"\n    (\w+): \{(.*?)\}(?=,\n    \w+: \{|\s*$)", block, re.S))


def test_every_archive_page_has_an_entry():
    assert set(entries()) == {"home", "podcast", "mcg", "elon", "finance", "threadguy"}


@pytest.mark.parametrize("name", ["home", "podcast", "mcg", "elon", "finance", "threadguy"])
def test_no_card_carries_a_number_that_can_go_stale(name):
    body = entries()[name]
    words = " ".join(re.findall(r'(?:room|head|sub): "([^"]*)"', body))
    assert not re.search(r"\d", words), f"{name}: {words}"


def test_no_em_dashes_in_the_copy():
    assert "—" not in TEMPLATE


@pytest.mark.parametrize("page, png", PAGES.items())
def test_every_page_shares_its_own_card_with_both_tags_together(page, png):
    html = (ROOT / "demo" / page).read_text()
    og = re.search(r'property="og:image" content="([^"]+)"', html).group(1)
    tw = re.search(r'name="twitter:image" content="([^"]+)"', html).group(1)
    assert og == tw, "twitter:image left behind shows the old card on X"
    assert og.startswith(f"{SITE}/demo/{png}?v="), og


@pytest.mark.parametrize("png", sorted(set(PAGES.values())))
def test_every_card_is_rendered_at_twice_the_scraper_size(png):
    head = (ROOT / "demo" / png).read_bytes()[:24]
    width, height = struct.unpack(">II", head[16:24])
    assert (width, height) == (2400, 1260), png


def test_the_renderer_draws_every_page_from_the_one_template():
    renderer = (ROOT / "scripts" / "make_og_image.mjs").read_text()
    for name, png in [("home", "og-home.png"), ("podcast", "og-broadcast.png"),
                      ("mcg", "og-mcg.png"), ("elon", "og-musk.png"),
                      ("finance", "og-finance.png"), ("threadguy", "og-threadguy.png")]:
        assert f'universal("{name}", "{png}")' in renderer

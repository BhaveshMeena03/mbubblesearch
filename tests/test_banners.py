"""The two header images: @mbubbleSearch's on X and $MBS's on DexScreener.

Both are drawn from HTML in demo/ by scripts/make_x_banner.mjs, and both
carry one figure, which has to be a floor the archive only grows past.
"""

import re
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"


@pytest.mark.parametrize("name", ["x-banner", "dex-banner"])
def test_each_banner_is_drawn_at_three_times_its_slot(name):
    assert Image.open(DEMO / f"{name}.png").size == (4500, 1500)


@pytest.mark.parametrize("name", ["x-banner", "dex-banner"])
def test_the_one_figure_is_a_floor_and_the_copy_has_no_dashes(name):
    html = (DEMO / f"{name}.html").read_text()
    body = html.split("<body>")[1]
    assert re.search(r"\d,\d00\+ hours", body), "the hours figure must be a floor"
    assert "—" not in html and "–" not in html


def test_nothing_on_the_x_banner_sits_under_the_avatar():
    """On the live profile the avatar covers x 44 to 395 from y 317 down,
    and it hid the first archive pill. The lower rows start right of it."""
    html = (DEMO / "x-banner.html").read_text()
    lower = re.search(r"\.lower\{[^}]*padding-left:(\d+)px", html)
    assert lower and int(lower.group(1)) > 395
    assert html.index('<div class="lower">') < html.index('<div class="rooms">')


def test_the_dexscreener_banner_is_the_x_header_centred():
    """Same design, but nothing is laid over a DexScreener header, so the
    rows the X header moves right of the avatar sit centred here."""
    dex = (DEMO / "dex-banner.html").read_text()
    x = (DEMO / "x-banner.html").read_text()
    for words in ("Ask anything. Jump to <em>the exact second</em>.", "Market Bubble</span>",
                  "The Record</span>", "with a question"):
        assert words in dex and words in x, words
    assert '<div class="lower">' not in dex

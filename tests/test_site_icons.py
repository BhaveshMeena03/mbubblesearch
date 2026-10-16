"""The site's icon: the @mbubbleSearch avatar, on every page that is ours.

Before this, two pages linked the bare green magnifier and the rest linked
nothing, so a tab showed the browser's blank page, and /favicon.ico was a
404 for every browser and crawler that asked.
"""

from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"

# Every page of this site. ClawPump's support page, the Bullpen Concierge
# and demo, the spend page and the banner template are someone else's
# brand or nobody's page, and keep their own.
PAGES = ["home", "podcast", "mcg", "elon", "threadguy", "finance", "rasmr", "posts",
         "calls", "guests", "guest", "assets", "mcg-assets", "threadguy-assets",
         "how-it-works"]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_carries_the_icon(page):
    head = (DEMO / f"{page}.html").read_text().split("</head>")[0]
    assert '<link rel="icon" type="image/png" sizes="48x48" href="/demo/icon-48.png">' in head
    assert '<link rel="icon" type="image/png" sizes="512x512" href="/demo/icon-512.png">' in head
    assert '<link rel="apple-touch-icon" href="/demo/icon-180.png">' in head
    assert "logo-200.png" not in head


@pytest.mark.parametrize("name, size, round_", [
    ("icon-48.png", 48, True), ("icon-96.png", 96, True), ("icon-512.png", 512, True),
    # iOS rounds a home-screen icon itself and paints clear corners black.
    ("icon-180.png", 180, False)])
def test_each_icon_is_its_size_and_shape(name, size, round_):
    im = Image.open(DEMO / name)
    assert im.size == (size, size)
    corner = im.convert("RGBA").getpixel((0, 0))
    assert (corner[3] == 0) == round_, corner
    centre = im.convert("RGB").getpixel((size // 2, size // 2))
    assert centre != (0, 0, 0)


def test_the_header_on_the_market_bubble_page_shows_it():
    page = (DEMO / "podcast.html").read_text()
    assert '<img src="/demo/icon-96.png" alt="" width="26" height="26">' in page


def test_the_root_answers_for_favicon_ico():
    from fastapi.testclient import TestClient

    from app import main
    with TestClient(main.app) as client:
        reply = client.get("/favicon.ico")
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "image/x-icon"
    assert reply.content[:4] == b"\x00\x00\x01\x00"

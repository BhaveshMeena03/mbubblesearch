"""Every archive reachable from a phone.

Found on a real phone: the header scrolled away, so past the top of any
page there was no way to switch archives; the Market Bubble page hid its
whole nav, switcher included, under 560px; The Record's top line was 545px
wide on a 390px screen; the menu opened off screen on a 320px phone; the
front door's stats left the fifth number alone on a line and its fine print
ran one word to a line.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAV = (ROOT / "demo" / "nav.js").read_text()
HOME = (ROOT / "demo" / "home.html").read_text()
PODCAST = (ROOT / "demo" / "podcast.html").read_text()
FINANCE = (ROOT / "demo" / "finance.html").read_text()


def test_the_bar_with_the_switcher_stays_on_a_phone():
    assert "@media (max-width:760px){" in NAV
    assert ".navbar{position:sticky;top:0;z-index:50" in NAV
    assert "bar.classList.add(\"navbar\")" in NAV


def test_its_background_does_not_clip_the_open_menu():
    """A shadow spread with a clip-path widened the background and clipped
    the menu to the bar, so it opened as a sliver."""
    assert "clip-path" not in NAV
    assert ".navbar::before{" in NAV and "left:var(--navx,0);width:100vw" in NAV


def test_a_page_without_a_header_pins_its_whole_top_row():
    assert 'var bar = host.closest("header");' in NAV
    assert "0.7 * window.innerWidth" in NAV


def test_the_menu_is_moved_onto_the_screen_by_exactly_its_overhang():
    assert "if (box.left + shift < 8) shift = 8 - box.left;" in NAV
    assert "max-width:calc(100vw - 16px)" in NAV


def test_the_market_bubble_page_keeps_its_switcher_on_a_phone():
    assert ".site-nav{display:none}" not in PODCAST
    assert "@media (max-width:560px){ .site-nav > a{display:none} }" in PODCAST


def test_the_front_door_reads_on_a_phone():
    assert '<div class="years"><b id="sc-span">2014 to 2026</b><span>span</span></div>' in HOME
    assert ".scale{display:grid;grid-template-columns:1fr 1fr" in HOME
    assert "footer{grid-template-columns:1fr;gap:22px}" in HOME
    assert "minmax(min(320px,100%),1fr)" in HOME


def test_the_record_top_line_fits_and_slims_when_pinned():
    assert ".topline nav.back { font-size: 14px; color: var(--ink-2); flex: none; }" in FINANCE
    assert ".topline.stuck #stat { display: none; }" in FINANCE

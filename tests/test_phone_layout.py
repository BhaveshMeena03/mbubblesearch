"""The pages on a phone.

Every one of these was a thing wrong on an iPhone on 2026-10-06, found by
opening each page at 375px and measuring it. They are checked here by
reading the page source, which cannot see a layout, so each test pins the
one rule whose absence caused the fault it names.
"""

from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent / "demo"
PAGES = ["podcast", "home", "mcg", "elon", "finance", "threadguy",
         "assets", "mcg-assets", "threadguy-assets"]


def page(name: str) -> str:
    return (DEMO / f"{name}.html").read_text()


NAV = (DEMO / "nav.js").read_text()


def test_one_phone_header_for_every_page_with_a_crumb():
    """MCG broke its title over two lines, the Musk page left "/" alone on
    a line, The Record ended its top line on a dangling "/"."""
    assert ".navbar.crumbed .brandline{display:contents}" in NAV
    assert ".navbar.crumbed .home-sep{display:none}" in NAV
    assert ".navbar.crumbed .mark b{white-space:nowrap}" in NAV
    assert 'bar.classList.add("crumbed")' in NAV


def test_a_wide_nav_goes_under_the_name_not_between_crumb_and_name():
    assert 'bar.classList.add("nav-wrapped")' in NAV
    assert ".navbar.crumbed.nav-wrapped .mark{order:2}" in NAV


def test_scrolled_the_phone_header_is_one_row():
    assert ".navbar.crumbed.stuck .mark" in NAV


def test_asking_on_a_phone_brings_the_answer_into_view():
    """The answer landed below the fold and nothing on screen changed."""
    assert "window.revealOnPhone = function" in NAV
    for name in ("podcast", "mcg", "elon", "finance", "threadguy", "home"):
        assert "revealOnPhone(" in page(name), name


def test_no_search_box_makes_an_iphone_zoom():
    """iOS Safari zooms the page when a field under 16px takes focus."""
    for name in ("mcg", "elon", "assets", "mcg-assets", "threadguy-assets"):
        assert "@media (max-width:760px){ #q{font-size:16px} }" in page(name), name


def test_passage_cards_cannot_grow_wider_than_the_screen():
    """A grid item will not shrink below its one-line title."""
    for name in ("home", "threadguy"):
        html = page(name)
        assert "grid-template-columns:minmax(0,1fr)" in html, name
        assert ".hit{min-width:0;" in html, name


def test_the_actions_under_a_passage_fit_a_phone():
    """Five in a flex row were wider than the screen: every label broke
    onto three lines and the page scrolled sideways."""
    html = page("podcast")
    assert ".hit-actions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))" in html
    assert ".hit-actions .watch{grid-column:1/-1" in html


def test_the_front_door_puts_the_answer_under_the_box_on_a_phone():
    html = page("home")
    assert "body.asked .hero .tries,body.asked .hero .scale{display:none}" in html
    assert html.count('document.body.classList.add("asked")') == 3


def test_the_record_asks_before_it_lists_who_is_in_it():
    html = page("finance")
    assert "header form { order: 3; }" in html
    assert "header .who { order: 5;" in html
    assert "#stat .long { display: none; }" in html


def test_the_market_bubble_page_has_no_band_above_its_header():
    html = page("podcast")
    assert ".wrap{padding-top:0}" in html
    assert "linear-gradient(to bottom,var(--bg) 0,var(--bg) 60px,transparent 170px)" in html


def test_every_page_names_its_colour_for_the_phones_own_bar():
    for name in PAGES:
        assert '<meta name="theme-color" content="#' in page(name), name

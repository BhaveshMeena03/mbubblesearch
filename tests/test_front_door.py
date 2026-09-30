"""The front door, guarded where it is cheap to guard.

The page is the one surface that touches every archive at once, and it
renders straight from the /v1/search payload. That makes it quietly
fragile: rename a field on the endpoint and the page keeps loading, keeps
looking finished, and silently stops linking anywhere.

That is not hypothetical. The page was first written against a `url`
field the endpoint has never returned. Every result rendered, every
timestamp looked clickable, and not one of them opened anything. Nothing
in the suite noticed, because nothing in the suite read the page.

So these assert the contract from both ends: the endpoint still lists the
four rooms the page draws meters for, and the page still reads the field
names the endpoint actually sends.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import _ROOMS  # noqa: E402

PAGE = (ROOT / "demo" / "home.html").read_text()


def test_the_page_draws_a_meter_for_every_room_the_endpoint_serves():
    for key, label, href in _ROOMS:
        assert f'k:"{key}"' in PAGE, f"no channel for {key}"
        assert f'href:"{href}"' in PAGE, f"{key} does not link to {href}"


def test_a_timestamp_reads_the_field_the_endpoint_sends():
    """`deep_link`, not `url`. The whole page hangs off this one name."""
    assert "h.deep_link" in PAGE
    assert "h.url" not in PAGE


def test_seconds_are_coerced_before_arithmetic():
    """start_seconds arrives as a string, so the embed start would read
    "1507.96" and YouTube would ignore it."""
    assert "Number(h.start_seconds)" in PAGE


def test_the_losing_archives_stay_on_the_page():
    """Routing is a judgement and is sometimes close. Showing only the
    winner would hide that, so the runners-up dim rather than disappear."""
    assert "considered" in PAGE
    assert "#chans.judged .chan{opacity:" in PAGE
    assert "display:none" not in PAGE.split("#chans.judged")[1][:400]


def test_no_em_dashes_in_the_copy():
    assert "—" not in PAGE


# The four below are the findings of an adversarial pass over the page,
# kept here because the browser battery that found them is not part of
# the suite and the failures it caught are all silent ones.


def test_a_failed_request_is_not_reported_as_an_empty_archive():
    """The endpoint sits behind four rate limiters and returns 503 when an
    archive is unloaded. Without an r.ok check the error body falls into
    render(), which finds no archive on it and tells the visitor that
    nothing in 1,187 hours matched. Being throttled and being unanswered
    must never read the same."""
    assert "if (!r.ok)" in PAGE
    assert "429:" in PAGE and "503:" in PAGE


def test_a_stalled_request_cannot_hang_the_button_forever():
    assert "AbortController" in PAGE
    assert "AbortError" in PAGE


def test_an_error_returns_the_board_to_rest():
    """Dimming the channels with no winner lit reads as broken rather
    than as an error, so trouble() puts them back to idle."""
    assert "function idle()" in PAGE
    assert 'classList.toggle("judged", !!winner)' in PAGE


def test_archive_text_cannot_push_the_page_sideways():
    """Titles, speaker names and transcript text all come from the
    archive, so none of their lengths are ours to assume. A flex child
    will not shrink below its content without min-width:0, and one long
    token was widening the page to 2089px inside a 375px viewport."""
    assert ".hit .row > *{min-width:0;overflow-wrap:anywhere}" in PAGE


def test_a_timestamp_is_reachable_without_a_mouse():
    assert 'tc.setAttribute("role", "button")' in PAGE
    assert 'tc.setAttribute("tabindex", "0")' in PAGE
    assert "tc.onkeydown" in PAGE
    assert "focus-visible" in PAGE


def test_only_one_recording_plays_at_a_time():
    assert "function stopPlaying()" in PAGE


def test_the_board_is_not_an_empty_instrument_at_rest():
    """Four rails with 'idle' written on them is a dashboard with nothing
    in it, which reads as unfinished rather than as ready. At rest the
    channels are a directory; the meters arrive with the first reading."""
    assert "#chans:not(.live) .bar{display:none}" in PAGE
    assert 'classList.add("live")' in PAGE
    # At rest each row says how big its archive is; that figure steps
    # aside for the score once there is one.
    assert "#chans.live .size{opacity:0" in PAGE


def test_the_page_shows_the_archive_rather_than_describing_it():
    """A search box on an empty page is a form, not a product, so the
    page carries what the shows actually talk about: every ticker they
    named, counted, with a recording and a second behind each one.

    The count and the moment travel together on purpose. A number with
    nothing to open is a claim the reader has to take on trust, and this
    page exists to not ask for that."""
    assert 'id="ranks"' in PAGE
    assert "var RANKS = [" in PAGE
    for field in ('"sym":', '"n":', '"vid":', '"at":', '"title":'):
        assert PAGE.count(field) >= 8, f"a ranked row is missing {field}"


def test_a_ranked_row_is_reachable_without_a_mouse():
    assert 'el.setAttribute("role", "button")' in PAGE
    assert "el.onkeydown" in PAGE


def test_a_recording_can_be_taken_fullscreen():
    """The embed carried no allowfullscreen, so the button inside the
    YouTube chrome was there and did nothing when pressed."""
    assert "f.allowFullscreen = true" in PAGE
    assert "allowfullscreen" in PAGE
    assert PAGE.count("fullscreen; picture-in-picture") >= 2


def test_switching_rows_fades_rather_than_snapping():
    """Rebuilding the block on every click flashed the whole stage away
    and back. It is built once and its contents are swapped behind a
    fade instead."""
    assert ".stage.on{opacity:1}" in PAGE
    assert "transition:opacity .3s var(--ease)" in PAGE
    assert "var fresh = st.hidden" in PAGE


def test_the_page_moves_in_one_language():
    """Half the page eased and half of it snapped. One easing token and
    two durations, so nothing moves in a way the rest of it does not."""
    assert "--ease:cubic-bezier(" in PAGE
    assert "--quick:" in PAGE and "--slow:" in PAGE
    # the answer used to appear instantly while everything around it moved
    assert "#out.shown{opacity:1;transform:none}" in PAGE
    assert 'classList.add("shown")' in PAGE
    # and a reader who asked for less motion still gets none of it
    assert "#out{transition:none;opacity:1;transform:none}" in PAGE


def test_an_answer_is_shown_even_in_a_background_tab():
    """The rise was triggered from requestAnimationFrame, which browsers
    do not run in a background tab. Switching away mid-search meant
    coming back to an answer that had arrived and would never appear.
    A forced reflow runs whether or not anybody is looking."""
    assert 'void $("out").offsetHeight' in PAGE
    assert "requestAnimationFrame(" not in PAGE  # the comment may name it


def test_hovering_a_ranked_row_does_not_move_the_rows_below_it():
    """The episode line was toggled from display:none on hover, which
    grew the row and shoved every row under it down. Hovering down the
    list made the whole thing jump. The line is always in the layout now
    and only its colour changes."""
    assert ".rank:hover .where,.rank.playing .where{color:var(--ink-3)}" in PAGE
    where = PAGE.split(".rank .where{")[1].split("}")[0]
    assert "display:none" not in where


def test_every_channel_card_points_at_a_real_archive():
    """They are the only way off this page, and a card that goes nowhere
    looks like a broken page rather than a missing route."""
    for key, label, href in _ROOMS:
        assert f'href:"{href}"' in PAGE
    assert PAGE.count('a.href = c.href') == 1


def test_the_hero_is_built_around_the_search_box():
    """A headline beside a rail of figures had no width that sat right:
    stretched, it left a hole down the middle; capped, it left one
    against the outside edge. The page is a search box, so the search
    box is the middle of it and everything lines up on that axis."""
    hero = PAGE.split(".hero{")[1].split("}")[0]
    assert "margin:0 auto" in hero and "text-align:center" in hero
    assert "heroGrid" not in PAGE          # the two column version is gone
    # the figures are a line under the search, not a column beside it
    assert ".scale{display:flex;justify-content:center" in PAGE


def test_the_demo_leads_with_the_question():
    """Bare quotes read fine and looked flat. The question is what turns
    three sentences somebody liked into a demonstration of the thing the
    page does, so it leads the card and the answer follows it."""
    assert 'id="moments"' in PAGE
    assert "var MOMENTS = [" in PAGE
    for field in ('q:"', 'said:"', 'who:"', 'vid:"', "at:"):
        assert PAGE.count(field) >= 3, f"a card is missing {field}"
    assert '.moment .q{' in PAGE


def test_the_sections_are_numbered_in_the_order_they_appear():
    """Inserting the demo above the ranking left two sections numbered
    01 for a while, which is the sort of thing only a reader notices."""
    for n, sel in (("01", "#asked-sect"), ("02", "#archive-sect"),
                   ("03", ".board"), ("04", "#behaves")):
        assert f'{sel} h2::before{{content:"{n}"}}' in PAGE \
            or f'{sel} h2::before{{content:"{n}"}} ' in PAGE, f"{sel} is not {n}"

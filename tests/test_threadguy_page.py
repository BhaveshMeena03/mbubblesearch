"""The ThreadGuy archive: its page, its answers and its wiring.

Built after the front door, and from the front door's hardened version
rather than an older page, so the guards here are the ones that page
earned the hard way. Plus two that only this archive has needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import podcast  # noqa: E402
from app.main import _ROOMS  # noqa: E402

PAGE = (ROOT / "demo" / "threadguy.html").read_text()
MAIN = (ROOT / "app" / "main.py").read_text()
PODCAST = (ROOT / "app" / "podcast.py").read_text()


def test_the_archive_answers_as_itself_and_not_as_the_broadcast():
    """Any namespace missing from the prompt map fell back to the Market
    Bubble prompt, which names Ansem and FaZe Banks as the hosts. Asked
    what ThreadGuy predicted, with his own stream titled "I Predicted..."
    as the top hit, the archive replied that the excerpts did not mention
    anyone called ThreadGuy."""
    assert '"threadguy": THREADGUY_SYSTEM_PROMPT' in PODCAST
    prompt = podcast.THREADGUY_SYSTEM_PROMPT
    assert "ThreadGuy hosts" in prompt
    assert "Ansem" not in prompt and "FaZe" not in prompt
    # the specific failure: absence of his name is not absence of him
    assert "his name is absent" in prompt


def test_a_view_is_dated_on_a_daily_show():
    """The market moves between streams, so a position from May is not
    his position in September and must not be presented as one."""
    assert "belongs to the day it was given" in podcast.THREADGUY_SYSTEM_PROMPT


def test_answers_written_under_the_wrong_prompt_are_not_served():
    """The cache key moves with the question, not the prompt, so the
    answers written under the Market Bubble prompt had to be orphaned
    explicitly or they would have gone on being served for a day."""
    assert 'surface="threadguy-v2"' in MAIN


def test_it_is_a_room_in_the_fan_out():
    assert ("threadguy", "ThreadGuy", "/threadguy") in _ROOMS
    assert '("/threadguy", "/demo/threadguy.html")' in MAIN


def test_the_page_says_what_failed_rather_than_that_nothing_matched():
    assert "if (!r.ok)" in PAGE
    assert "429:" in PAGE and "503:" in PAGE
    assert "AbortController" in PAGE and "AbortError" in PAGE


def test_nothing_waits_on_a_background_tab():
    """Transitions and timers are throttled in a hidden tab, so the reveal
    and the streamed answer both have a path that does not depend on them."""
    assert 'classList.toggle("instant", document.hidden)' in PAGE
    assert "if (document.hidden) { el.textContent = text; return; }" in PAGE
    assert "requestAnimationFrame(" not in PAGE


def test_everything_clickable_is_reachable_without_a_mouse():
    assert "function pressable(" in PAGE
    assert 'el.setAttribute("role", "button")' in PAGE
    assert "el.onkeydown" in PAGE


def test_one_recording_plays_at_a_time_and_can_go_fullscreen():
    assert "function stopPlaying()" in PAGE
    assert "f.allowFullscreen = true" in PAGE


def test_show_more_does_not_repeat_a_month():
    """The Show more button was the last element when the next batch was
    drawn, carried no month, and so every press opened a second heading
    for the month already on screen."""
    draw = PAGE.split("function drawTo(n){")[1].split("function ")[0]
    assert draw.index('old.remove()') < draw.index("host.lastElementChild")


def test_the_page_says_it_is_independent():
    assert "not affiliated with ThreadGuy" in PAGE


def test_no_em_dashes():
    assert "—" not in PAGE
    assert "—" not in podcast.THREADGUY_SYSTEM_PROMPT


def test_it_is_its_own_room_and_not_the_front_door_reskinned():
    """The first version was the front door's centred hero in navy. The
    archive pages share a structure instead: the room's name in the
    corner, a readout strip, a left aligned headline, and a picture of
    their own on the right. Here the picture is the latest recordings as
    days, because on a daily show the date is the point."""
    assert 'class="strip' in PAGE
    assert 'id="days"' in PAGE and "function drawDays(" in PAGE
    hero = PAGE.split(".hero{")[1].split("}")[0]
    assert "grid-template-columns" in hero and "text-align:center" not in hero


def test_a_latest_tile_plays_on_the_stage_not_in_the_tile():
    """In a column this narrow a video inside the tile would be the width
    of a thumbnail."""
    days = PAGE.split("function drawDays(")[1].split("function drawTo(")[0]
    assert '$("tgstage")' in days
    assert "play(el" not in days


def test_the_column_says_recordings_because_not_all_are_streams():
    assert "latest recordings" in PAGE and "latest streams" not in PAGE


def test_a_link_to_the_archive_shares_with_a_picture():
    """It shipped with no og:image and a summary card, so a link to
    /threadguy rendered as a bare line of text in anybody's timeline."""
    assert 'property="og:image" content="https://search.lexthedev.com/demo/og-threadguy.png?v=2"' in PAGE
    assert 'name="twitter:card" content="summary_large_image"' in PAGE
    assert (ROOT / "demo" / "og-threadguy.png").exists()
    assert (ROOT / "demo" / "og-threadguy-card.html").exists()


def test_llms_txt_knows_about_threadguy():
    """It had no mention of ThreadGuy at all, and it is the file an agent,
    or a judge, reads first."""
    llms = (ROOT / "demo" / "llms.txt").read_text()
    assert "Five archives" in llms
    assert "https://search.lexthedev.com/threadguy" in llms
    assert "/v1/threadguy/search" in llms


def test_the_page_claims_only_what_the_archive_holds():
    """It said "Every market open" and "every stream". 54 of 541 recordings
    are market opens and 435 are interviews, and most of his daily streams
    are on Twitch and never reach YouTube, which is all this indexes."""
    for claim in ("Every market open", "every stream", "Every ThreadGuy stream"):
        assert claim not in PAGE, claim
    card = (ROOT / "demo" / "og-threadguy-card.html").read_text()
    assert "Every market open" not in card and "Every ThreadGuy stream" not in card

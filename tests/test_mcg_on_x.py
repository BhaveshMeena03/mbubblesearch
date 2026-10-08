"""An MCG Live citation that opens on X, at the same second.

MCG sends one recording to YouTube and to X. The archive is the YouTube
copy, so these check the two things that could send somebody to the wrong
place: pairing a show with a broadcast that is not it, and landing on the
wrong second of one that is.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import mcg_on_x  # noqa: E402
from app.x_bot import format_reply  # noqa: E402
from scripts.pair_mcg_broadcasts import (  # noqa: E402
    candidates,
    listening_points,
    offset,
    settle,
    title_words,
)

PAIRS = {"miXTiMNMDCc": {"broadcast": "https://x.com/i/broadcasts/1AGRnZrnNwzGl",
                         "offset": -2, "post": "2107865263060508725"}}


@pytest.fixture(autouse=True)
def paired(tmp_path, monkeypatch):
    path = tmp_path / "mcg_broadcast_links.json"
    path.write_text(json.dumps(PAIRS))
    monkeypatch.setattr(mcg_on_x, "_PATH", path)
    mcg_on_x._pairs.cache_clear()
    yield
    mcg_on_x._pairs.cache_clear()


# --- the link ---------------------------------------------------------------

def test_a_paired_show_opens_on_x_at_the_same_moment():
    link = mcg_on_x.on_x("https://www.youtube.com/watch?v=miXTiMNMDCc&t=2400s")
    assert link == "https://x.com/i/broadcasts/1AGRnZrnNwzGl?t=2398"


def test_a_show_that_was_not_on_x_keeps_its_youtube_link():
    link = "https://www.youtube.com/watch?v=cHos0BsSoFM&t=2970s"
    assert mcg_on_x.on_x(link) == link


def test_a_link_with_no_second_is_left_alone():
    """The broadcast would open on the waiting screen of a four hour show."""
    link = "https://www.youtube.com/watch?v=miXTiMNMDCc"
    assert mcg_on_x.on_x(link) == link


def test_what_is_not_a_youtube_link_is_left_alone():
    for link in ("https://x.com/i/broadcasts/1rGmqpnbjvnGy?t=5781", "", None):
        assert mcg_on_x.on_x(link) == link


def test_the_first_seconds_of_a_show_do_not_go_below_zero():
    link = mcg_on_x.on_x("https://www.youtube.com/watch?v=miXTiMNMDCc&t=1s")
    assert link.endswith("?t=0")


def test_a_broken_file_costs_the_link_and_not_the_reply(tmp_path, monkeypatch):
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    monkeypatch.setattr(mcg_on_x, "_PATH", path)
    mcg_on_x._pairs.cache_clear()
    link = "https://www.youtube.com/watch?v=miXTiMNMDCc&t=2400s"
    assert mcg_on_x.on_x(link) == link


@dataclass
class McgHit:
    title: str = "🔴 LIVE: Onchain Volume Falling Across the Board | Why Stay Bullish?"
    timestamp: str = "40:00"
    deep_link: str = "https://www.youtube.com/watch?v=miXTiMNMDCc&t=2400s"
    text: str = "volume is falling across the board"
    text_ts: str = "[40:00] volume is falling across the board"


def test_the_bot_links_the_x_copy_in_a_reply():
    reply = format_reply("Onchain volume is falling across the board.",
                         [McgHit()], include_links=True)
    assert "https://x.com/i/broadcasts/1AGRnZrnNwzGl?t=2398" in reply
    assert "youtube.com" not in reply


# --- which broadcast is which show ------------------------------------------

SHELF = [
    {"id": "cHos0BsSoFM", "format": "stream", "published_at": "2026-10-06",
     "title": "🔴 LIVE: S&P New ATH, BTC Stuck at $86K | Crypto Uptober Canceled?"},
    {"id": "ibrbS5qnFYA", "format": "video", "published_at": "2026-10-06",
     "title": "Is this FUTARDIO V2!?| Backable"},
    {"id": "lN2kZJ0CNZg", "format": "stream", "published_at": "2026-10-02",
     "title": "🟢 LIVE: Robinhood Chain Rally Incoming? How to Position"},
]


def test_the_post_and_the_stream_have_the_same_title_under_the_markup():
    post = "🔴 LIVE: S&amp;P New ATH, BTC Stuck at $86K | Crypto Uptober Canceled? https://t.co/k1"
    assert title_words(post) == title_words(SHELF[0]["title"])


def test_the_show_with_the_same_title_is_tried_first():
    post = {"created_at": "2026-10-06T16:12:00.000Z",
            "text": "🔴 LIVE: S&amp;P New ATH, BTC Stuck at $86K | Crypto Uptober Canceled? https://t.co/k1"}
    assert [r["id"] for r in candidates(post, SHELF, set())] == ["cHos0BsSoFM"]


def test_a_retitled_show_is_still_a_candidate_by_the_day_it_aired():
    """2 October went out on X as "Bitcoin Consolidating into Uptober" and
    sits on YouTube as "Robinhood Chain Rally Incoming". Same recording."""
    post = {"created_at": "2026-10-02T16:14:00.000Z",
            "text": "🟢 LIVE: Bitcoin Consolidating into Uptober | Higher Soon? https://t.co/k2"}
    assert [r["id"] for r in candidates(post, SHELF, set())] == ["lN2kZJ0CNZg"]


def test_an_interview_uploaded_that_day_is_not_a_candidate():
    post = {"created_at": "2026-10-06T16:12:00.000Z", "text": "🔴 LIVE: something else"}
    assert "ibrbS5qnFYA" not in [r["id"] for r in candidates(post, SHELF, set())]


def test_a_show_already_paired_is_not_offered_again():
    post = {"created_at": "2026-10-06T16:12:00.000Z", "text": "🔴 LIVE: something else"}
    assert candidates(post, SHELF, {"cHos0BsSoFM"}) == []


# --- how far apart the clocks are -------------------------------------------

def lines(count: int, start: float = 0.0, word: str = "w") -> list[dict]:
    """Eight-second lines of eight words, no word used twice."""
    return [{"t": start + 8 * i,
             "text": " ".join(f"{word}{i}x{n}" for n in range(8))}
            for i in range(count)]


def heard(indexed: list[dict], at: int, gap: float, count: int = 11) -> list[dict]:
    """What a slice taken at `at` on X would hear, on a clock from zero."""
    wanted = [s for s in indexed if s["t"] + gap >= at][:count]
    return [{"start": s["t"] + gap - at, "end": s["t"] + gap - at + 8,
             "text": s["text"]} for s in wanted]


def test_the_gap_between_the_two_clocks_is_measured():
    indexed = lines(400)
    assert offset(heard(indexed, 1200, -2.0), 1200, indexed) == pytest.approx(-2.0, abs=0.01)


def test_a_slice_of_some_other_show_is_not_evidence():
    assert offset(lines(11, word="other"), 1200, lines(400)) is None


def test_a_few_words_in_common_are_not_evidence():
    indexed = lines(400)
    assert offset(heard(indexed, 1200, -2.0, count=2), 1200, indexed) is None


def test_runs_that_disagree_with_each_other_are_not_evidence():
    indexed = lines(400)
    slice_ = heard(indexed, 1200, -2.0, count=6) + [
        {**s, "start": s["start"] + 300, "end": s["end"] + 300}
        for s in heard(indexed, 1300, -2.0, count=6)]
    assert offset(slice_, 1200, indexed) is None


def test_both_ends_of_the_show_have_to_agree():
    assert settle(-1.7, -1.6) == -2
    assert settle(0.8, 1.1) == 1
    # A stream that dropped and came back: right at the start, minutes out
    # by the end.
    assert settle(-1.7, 240.0) is None
    assert settle(-1.7, None) is None
    assert settle(None, None) is None


def test_a_long_show_is_heard_past_the_waiting_screen_and_before_the_end():
    assert listening_points(16429) == [1200, 14629]
    assert listening_points(1800) == [600, 1200]

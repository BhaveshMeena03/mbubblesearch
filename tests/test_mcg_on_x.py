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
    PRECISION,
    candidates,
    chart,
    listening_points,
    offset,
    shape,
    since,
    stretches,
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


def test_a_long_show_is_heard_every_twenty_minutes_to_half_an_hour_from_the_end():
    assert listening_points(11675) == [1200, 2400, 3600, 4800, 6000, 7200, 8400, 9600, 9875]
    assert listening_points(3600) == [1200, 1800]
    assert listening_points(1800) == [600, 1200]


# --- a show whose clocks move -----------------------------------------------

def step(changes: list[tuple[int, float | None]]):
    """A broadcast whose offset is the last change at or before `at`, and
    a record of every second that was listened to."""
    asked: list[int] = []

    def listen(at: int) -> float | None:
        asked.append(at)
        return [gap for start, gap in changes if start <= at][-1]
    return listen, asked


def test_a_show_that_keeps_one_offset_is_stored_as_one_number():
    listen, asked = step([(0, -1.6)])
    found = chart(listen, listening_points(11675))
    assert shape(stretches(found)) == {"offset": -2}
    assert len(asked) == 9          # nothing to narrow, nothing heard twice


def test_a_jump_is_pinned_and_each_side_keeps_its_own_offset():
    """26 October: -1.5 to 2:20, -20 by 2:44."""
    listen, asked = step([(0, -1.5), (9000, -20.0)])
    found = chart(listen, listening_points(11675))
    sections = stretches(found)
    assert [s["offset"] for s in sections] == [-2, -20]
    assert sections[0]["from"] == 0 and sections[-1]["to"] is None
    # The change is somewhere in the gap, and the gap is a few minutes.
    assert sections[0]["to"] <= 9000 + 2 < sections[1]["from"] + 20
    assert sections[1]["from"] - sections[0]["to"] <= PRECISION + 20
    assert len(asked) <= 9 + 6


def test_a_stretch_that_is_not_the_show_is_left_unmapped():
    """23 October was not the show at 1:00 and 1:40 on X, and was -2
    before and -41 after."""
    listen, _ = step([(0, -2.0), (3000, None), (7000, -41.0)])
    sections = stretches(chart(listen, listening_points(11033)))
    assert [s["offset"] for s in sections] == [-2, -41]
    assert sections[0]["to"] < 3000 + 90
    assert sections[1]["from"] > 7000


def test_a_third_story_in_the_middle_stops_the_narrowing():
    listen, asked = step([(0, -2.0), (5000, 30.0), (5100, -40.0)])
    found = chart(listen, [1200, 9000])
    assert len(asked) < 10
    assert stretches(found) is not None


def test_one_slice_that_matches_is_not_enough_to_call_it_the_show():
    assert stretches([(1200, -2.0), (9000, None)]) is None
    assert stretches([(1200, None), (9000, None)]) is None


def test_a_show_heard_as_something_else_at_the_start_does_not_open_at_zero():
    sections = stretches([(1200, None), (2400, -3.0), (3600, -3.0)])
    assert sections == [{"from": 2403, "to": None, "offset": -3}]


SECTIONS = {"4PlcWhQM96A": {
    "broadcast": "https://x.com/i/broadcasts/1DxLdZzgDnLxm", "post": "2114751097718145346",
    "sections": [{"from": 0, "to": 9000, "offset": -2},
                 {"from": 9150, "to": None, "offset": -20}]}}


@pytest.fixture
def jumped(tmp_path, monkeypatch):
    path = tmp_path / "mcg_broadcast_links.json"
    path.write_text(json.dumps({**PAIRS, **SECTIONS}))
    monkeypatch.setattr(mcg_on_x, "_PATH", path)
    mcg_on_x._pairs.cache_clear()


def test_each_side_of_a_jump_opens_at_its_own_second(jumped):
    before = mcg_on_x.on_x("https://www.youtube.com/watch?v=4PlcWhQM96A&t=878s")
    after = mcg_on_x.on_x("https://www.youtube.com/watch?v=4PlcWhQM96A&t=10000s")
    assert before == "https://x.com/i/broadcasts/1DxLdZzgDnLxm?t=876"
    assert after == "https://x.com/i/broadcasts/1DxLdZzgDnLxm?t=9980"


def test_a_second_where_the_clocks_moved_keeps_its_youtube_link(jumped):
    link = "https://www.youtube.com/watch?v=4PlcWhQM96A&t=9100s"
    assert mcg_on_x.on_x(link) == link


def test_a_show_with_one_offset_still_reads_as_before(jumped):
    link = mcg_on_x.on_x("https://www.youtube.com/watch?v=miXTiMNMDCc&t=2400s")
    assert link == "https://x.com/i/broadcasts/1AGRnZrnNwzGl?t=2398"


def test_unreadable_sections_cost_the_link_and_not_the_reply(tmp_path, monkeypatch):
    path = tmp_path / "mcg_broadcast_links.json"
    path.write_text(json.dumps({"4PlcWhQM96A": {
        "broadcast": "https://x.com/i/broadcasts/1DxLdZzgDnLxm",
        "sections": [{"to": 9000, "offset": -2}, "nonsense"]}}))
    monkeypatch.setattr(mcg_on_x, "_PATH", path)
    mcg_on_x._pairs.cache_clear()
    link = "https://www.youtube.com/watch?v=4PlcWhQM96A&t=878s"
    assert mcg_on_x.on_x(link) == link


# --- where the search starts ------------------------------------------------

def test_the_search_starts_after_the_newest_post_paired():
    pairs = {"a": {"post": "2114751097718145346"}, "b": {"post": "2113301916814061956"}}
    posted = ((2114751097718145346 >> 22) + 1288834974657) / 1000
    assert since(pairs, now=posted + 86400) == "2114751097718145346"


def test_a_post_older_than_the_search_reaches_is_not_passed_to_x():
    """X answers 400 to a since_id older than seven days."""
    pairs = {"a": {"post": "2112577368946876521"}}
    posted = ((2112577368946876521 >> 22) + 1288834974657) / 1000
    assert since(pairs, now=posted + 7 * 86400) is None


def test_a_pair_made_by_hand_without_a_post_is_skipped():
    assert since({"a": {"broadcast": "x"}}) is None
    pairs = {"a": {"broadcast": "x"}, "b": {"post": "2114751097718145346"}}
    posted = ((2114751097718145346 >> 22) + 1288834974657) / 1000
    assert since(pairs, now=posted + 60) == "2114751097718145346"

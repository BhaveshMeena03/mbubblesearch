"""When the watcher decides the show has ended.

This decision starts a twenty minute transcription on a laptop at six in
the morning with nobody watching, so it gets tested rather than trusted.

The signal is the video's own reported length. The post exists from the
first minute of the stream, so its presence proves nothing; the duration
grows while the show is live and stops when X swaps the live feed for the
finished recording.

Getting it wrong in either direction has a cost. Too eager and the
archive holds the first hour of a four hour show, stored as though it
were the episode, with nothing that ever re-checks it. Too cautious and
the show is not searchable until somebody wakes up, which is the problem
this was built for.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.watch_for_broadcast import (  # noqa: E402
    READY,
    WAIT_FIRST,
    WAIT_GROWING,
    WAIT_SHORT,
    verdict,
)

H = 3_600_000


def test_a_stream_that_just_started_is_not_ready():
    assert verdict(20 * 60_000, None) == WAIT_SHORT


def test_a_long_reading_still_needs_a_second_one():
    """One reading cannot distinguish a finished show from a live one that
    happens to be three hours in."""
    assert verdict(3 * H, None) == WAIT_FIRST


def test_a_growing_video_is_a_live_one():
    assert verdict(3 * H + 900_000, 3 * H) == WAIT_GROWING


def test_two_equal_readings_past_the_minimum_mean_it_ended():
    assert verdict(4 * H, 4 * H) == READY


def test_a_cut_down_never_becomes_ready():
    """45 minutes, unchanged forever. It must not trip the ready state
    just by sitting still."""
    assert verdict(45 * 60_000, 45 * 60_000) == WAIT_SHORT


def test_the_minimum_is_adjustable():
    assert verdict(1 * H, 1 * H, min_hours=0.5) == READY


def test_a_shrinking_reading_counts_as_ended():
    """Not expected, but if X revises the number downward the video is
    certainly not still growing, and refusing forever would be worse."""
    assert verdict(3 * H, 4 * H) == READY


def test_a_whole_thursday_night():
    """The real sequence, at fifteen minute intervals: the stream opens,
    runs about four hours, and X finishes the recording.

    Nothing may report ready while the number is still climbing, and it
    must report ready on the first repeat after it stops.
    """
    # 02:05 IST start, growing 15 min per poll, ending at 4h.
    growing = [i * 900_000 for i in range(1, 17)]      # 15min .. 4h
    settled = [16 * 900_000] * 3                        # X stops updating
    readings = growing + settled

    previous, states = None, []
    for reading in readings:
        states.append(verdict(reading, previous))
        previous = reading

    assert READY not in states[:len(growing)], (
        "called it ready while the show was still going")
    assert states[len(growing)] == READY, (
        "did not notice the first time the number stopped moving")
    # 4h at 15-minute polls: found within a quarter hour of the real end.
    assert states.index(READY) == len(growing)


# --- a short show is still a show ---------------------------------------------

def test_a_short_show_linked_from_the_player_is_finished_when_it_stops():
    """9 October 2026, 74 minutes. The watcher said "still short" for nine
    hours over a recording that had ended before its first poll."""
    from scripts.watch_for_broadcast import floor_for

    show = 4_447_000                                    # milliseconds, measured
    live = floor_for(True, 2.5)
    assert verdict(show, None, live) == WAIT_FIRST
    assert verdict(show, show, live) == READY
    assert verdict(show + 60_000, show, live) == WAIT_GROWING


def test_an_attached_video_keeps_the_floor_that_stops_a_cut_down():
    from scripts.watch_for_broadcast import floor_for

    assert floor_for(False, 2.5) == 2.5
    assert verdict(74 * 60_000, 74 * 60_000, floor_for(False, 2.5)) == WAIT_SHORT


def test_a_manifest_that_could_not_be_read_is_not_a_finished_show():
    """replay_ms returns 0 when the read fails, twice in a row as easily
    as once, and two equal zeros must not start an index."""
    from scripts.watch_for_broadcast import floor_for

    assert verdict(0, 0, floor_for(True, 2.5)) == WAIT_SHORT
    assert verdict(5 * 60_000, 5 * 60_000, floor_for(True, 2.5)) == WAIT_SHORT


def test_the_watcher_knows_which_kind_of_post_it_found():
    source = (Path(__file__).resolve().parent.parent
              / "scripts" / "watch_for_broadcast.py").read_text()
    assert 'return post["id"], replay_ms(link), True' in source
    assert 'return post["id"], longest, False' in source
    assert "floor_for(live, args.min_hours)" in source

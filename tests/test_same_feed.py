"""scripts/same_feed.py: Ansem's broadcast of a Market Bubble episode
against Market Bubble's own, and the clock between them.

Nothing here reads X: the playlists and the sound are made up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import same_feed as feed  # noqa: E402

READINGS = 100                          # loudness readings a second
WENT_OUT = 1793305977.8                 # Episode 24, 29 Oct 2026, 20:32:57 UTC


def _chunks(seconds: int, lost: tuple[int, int] | None = None, start: float = WENT_OUT,
            jitter: dict[int, float] | None = None) -> list[tuple[float, float, float]]:
    """A replay's chunks, two seconds each. `lost` (at, seconds): the
    replay skips that much of what went out at second `at` of itself."""
    out, position, wall = [], 0.0, start
    while position < seconds:
        if lost and position == lost[0]:
            wall += lost[1]
        out.append((position, wall + (jitter or {}).get(int(position), 0.0), 2.0))
        position, wall = position + 2.0, wall + 2.0
    return out


def test_two_replays_of_one_feed_share_a_clock():
    assert feed.clock(_chunks(10920), _chunks(10984)) == {"offset": 0}


def test_their_lost_minute_moves_his_clock_after_it():
    """Episode 24: Market Bubble's replay skips 62 seconds at 2:23:13 that
    Ansem's kept, which is why his runs 63 seconds longer."""
    found = feed.clock(_chunks(10920, lost=(8594, 62)), _chunks(10984))
    assert found == {"sections": [{"from": 0, "to": 8594, "offset": 0},
                                  {"from": 8594, "to": None, "offset": 62}]}


def test_a_stretch_he_lost_has_no_clock():
    """Their seconds that went out while his replay had a hole open nowhere
    on his; a wrong second would be worse than none."""
    stretches = feed.clock(_chunks(10920), _chunks(10860, lost=(3000, 60)))["sections"]
    assert stretches[0] == {"from": 0, "to": 3000, "offset": 0}, "not a second into the hole"
    assert stretches[1]["from"] >= 3060 and stretches[1]["offset"] == -60
    assert not any(s["from"] <= 3030 < (s["to"] or 10 ** 6) for s in stretches)


def test_a_stretch_both_lost_is_one_clock_not_two():
    """The 16 Jul episode's two replays both skip at 20:56, and came out as two
    sections that agreed."""
    assert feed.clock(_chunks(10920, lost=(1256, 5)), _chunks(10920, lost=(1256, 5))) == {"offset": 0}


def test_a_late_start_is_a_shift_not_a_new_show():
    """His went live five seconds after theirs: his seconds are earlier."""
    assert feed.clock(_chunks(10920), _chunks(10920, start=WENT_OUT + 5)) == {
        "sections": [{"from": 5, "to": None, "offset": -5}]}, "no clock before his began"


def test_a_lone_stamp_two_seconds_out_is_not_a_lost_stretch():
    """Ansem's Episode 24 has six of these: a chunk stamped 1.9 seconds
    late and the next one back on time."""
    copy = _chunks(10984, jitter={234: 1.93, 3066: 1.97, 4070: 1.75, 6696: 1.77})
    assert feed.clock(_chunks(10920), copy) == {"offset": 0}


def test_something_else_he_broadcast_another_day_is_no_copy():
    assert feed.clock(_chunks(10920), _chunks(10920, start=WENT_OUT + 86400)) is None


def test_ten_minutes_in_common_is_too_little_to_call_it_a_copy():
    assert feed.clock(_chunks(10920), _chunks(500, start=WENT_OUT + 10000)) is None


PLAYLIST = """#EXTM3U
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:4
#EXT-X-PROGRAM-DATE-TIME:2026-10-22T20:40:26.944Z
#EXTINF:2.002,
chunk_1792701625627801679_0_a.ts
#EXT-X-PROGRAM-DATE-TIME:2026-10-22T20:40:28.445Z
#EXTINF:2.002,
chunk_1792701625637653358_1_a.ts
#EXT-X-ENDLIST
"""


def test_a_playlist_is_read_chunk_by_chunk():
    first, second = feed.parse(PLAYLIST)
    assert first[0] == 0.0 and first[2] == pytest.approx(2.002)
    assert second[0] == pytest.approx(2.002)
    assert second[1] - first[1] == pytest.approx(1.501)


@pytest.mark.parametrize("playlist", [
    "",
    PLAYLIST.replace("#EXT-X-PROGRAM-DATE-TIME:2026-10-22T20:40:28.445Z\n", ""),
    PLAYLIST.replace("2026-10-22T20:40:26.944Z", "yesterday"),
    PLAYLIST.replace("#EXTINF:2.002,", "#EXTINF:abc,", 1),
    PLAYLIST.replace("#EXTINF:2.002,", "#EXTINF:-2,", 1),
])
def test_a_playlist_not_stamped_throughout_is_not_read_at_all(playlist):
    assert feed.parse(playlist) is None


# --- the sound -------------------------------------------------------------

def _show(seconds: int, seed: int = 1) -> np.ndarray:
    """A made-up show's loudness: speech-like bursts and pauses."""
    rng = np.random.default_rng(seed)
    return np.convolve(rng.random(seconds * READINGS) ** 3, np.ones(20) / 20, mode="same")


def test_a_stretch_is_found_where_it_is():
    show = _show(300)
    assert feed.line_up(show[12000:16000], show) == pytest.approx(120.0)


def test_another_encoding_of_the_same_sound_is_still_found():
    """Two broadcasts of one feed are encoded apart, so no reading is equal."""
    show = _show(300)
    copy = show * 1.3 + 0.02 + np.random.default_rng(7).normal(0, 0.01, show.size)
    assert feed.line_up(show[5000:9000], copy) == pytest.approx(50.0)


def test_a_different_show_is_not_found_anywhere():
    assert feed.line_up(_show(40, seed=2), _show(300, seed=3)) is None


def test_silence_is_never_a_match():
    assert feed.line_up(np.zeros(4000), _show(300)) is None


def test_a_stretch_that_repeats_is_not_placed_on_a_guess():
    """MCG's intro loops, and once paired the 29 Oct show with the 28th's.
    Two equally good places is no place."""
    bit = _show(40)
    hay = np.concatenate([bit, _show(60, seed=5), bit, _show(60, seed=6)])
    assert feed.line_up(bit, hay) is None


def _sounds(monkeypatch, answer):
    asked = []

    def offset_at(show, copy, t, near):
        asked.append(t)
        return answer(t, near)
    monkeypatch.setattr(feed, "offset_at", offset_at)
    return asked


LOST_MINUTE = {"sections": [{"from": 0, "to": 8594, "offset": 0},
                            {"from": 8594, "to": None, "offset": 63}]}


def test_the_sound_has_the_last_word(monkeypatch):
    asked = _sounds(monkeypatch, lambda t, near: near + 0.3)
    assert feed.confirm("show", "copy", LOST_MINUTE, say=lambda _: None)
    assert asked == [4277], "the middle of the longest stretch, once"


def test_stamps_that_line_up_with_other_sound_are_refused(monkeypatch):
    """Something else he broadcast at that hour has the same stamps."""
    _sounds(monkeypatch, lambda t, near: None)
    assert not feed.confirm("show", "copy", {"offset": 0}, say=lambda _: None)


def test_sound_a_few_seconds_from_the_stamps_is_refused(monkeypatch):
    _sounds(monkeypatch, lambda t, near: near + 5)
    assert not feed.confirm("show", "copy", LOST_MINUTE, say=lambda _: None)


def test_a_quiet_moment_is_tried_elsewhere_before_giving_up(monkeypatch):
    asked = _sounds(monkeypatch, lambda t, near: None if t == 4277 else near)
    assert feed.confirm("show", "copy", LOST_MINUTE, say=lambda _: None)
    assert len(asked) == 2


def test_x_refusing_a_stream_is_no_clock(monkeypatch):
    monkeypatch.setattr(feed, "stream", lambda post: None)
    assert feed.measure("2115904745818996865", "2115904746355851479", say=lambda _: None) is None


def test_a_playlist_with_no_stamps_is_no_clock(monkeypatch):
    monkeypatch.setattr(feed, "stream", lambda post: ("1PJqrNvWzwexb", "https://x/a.m3u8"))
    monkeypatch.setattr(feed, "chunks", lambda media: None)
    assert feed.measure("2115904745818996865", "2115904746355851479", say=lambda _: None) is None

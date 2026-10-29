"""Titles for X episodes, and hosts who were not in the room."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_speaker_map import ABSENT, may_name  # noqa: E402
from scripts.fetch_x_episodes import title_from  # noqa: E402

TOKEN2049 = ("Our full conversation with Mayne and Avi Felman at TOKEN2049 in "
             "Singapore:")


def test_a_flattened_chapter_list_stays_out_of_the_title():
    """What yt-dlp handed back for the 19 Oct 2026 post: no line breaks."""
    meta = {"description": TOKEN2049 + " 0:08 Why is everyone so bullish? "
            "3:44 Finance and Crypto are converging 9:38 Trading vs making "
            "investments https://t.co/OiyHtoLjyk"}
    assert title_from(meta) == TOKEN2049.rstrip(":")


def test_the_same_post_with_its_line_breaks_gets_the_same_title():
    meta = {"description": TOKEN2049 + "\n\n0:08 Why is everyone so bullish?"}
    assert title_from(meta) == TOKEN2049.rstrip(":")


def test_titles_that_were_already_right_are_unchanged():
    assert title_from({"description": "Our full conversation with Orangie. "
                       "0:00 Intro"}) == "Our full conversation with Orangie."
    assert title_from({"description": "LIVE W/ JOHANN KERBRAT, IVAN "
                       "SOTO-WRIGHT, & AUSTIN BARACK: Market Bubble Ep 13"}) \
        == "LIVE W/ JOHANN KERBRAT, IVAN SOTO-WRIGHT, & AUSTIN BARACK: Market Bubble Ep 13"


def test_an_escaped_ampersand_in_the_post_is_not_in_the_title():
    """X's post text for Ansem's 29 Oct 2026 video, as yt-dlp returned it."""
    meta = {"description": "market thoughts, trading breakouts &amp; breakdowns, "
            "candlesticks as fractals\n\n0:00 - market bleeding and the $ETH short"}
    assert title_from(meta) == ("market thoughts, trading breakouts & breakdowns, "
                                "candlesticks as fractals")


def test_a_host_who_was_not_on_stage_is_never_named():
    assert not may_name("x-2112232289178722440", "FaZe Banks")
    assert may_name("x-2112232289178722440", "Ansem")
    assert may_name("x-2095611959161082020", "FaZe Banks")
    assert all(names for names in ABSENT.values())


def test_a_passage_whose_labels_are_all_removed_is_rewritten_plain():
    from app.schemas import Episode
    from scripts.apply_speaker_labels import passage_updates
    episode = Episode(episode_id="x-1", title="t", url="u", platform="other",
                      published_at="2026-10-19",
                      segments=[{"t": float(i), "text": f"line {i}"}
                                for i in range(6)])
    labelled = passage_updates(episode, {"0": "FaZe Banks"}, 10_000)
    cleared = passage_updates(episode, {}, 10_000)
    assert labelled and labelled[0][2] == ["FaZe Banks"]
    assert "FaZe Banks:" in labelled[0][1]
    # Same passage, same id, written again with no name on it.
    assert [c[0] for c in cleared] == [w[0] for w in labelled]
    assert cleared[0][2] == [] and "FaZe Banks" not in cleared[0][1]

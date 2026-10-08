"""The front door's numbers come from the archives, not from a file.

Two of the five archives are indexed every morning. The totals used to be
typed into home.html and llms.txt and corrected by hand: on 9 October 2026
they said 1,967 hours and 1,328 recordings while the archives held 2,040
and 1,377, and llms.txt listed 14 Musk interviews of 24.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import _LLMS_FIGURE, render_llms, total_sizes  # noqa: E402

HOME = (ROOT / "demo" / "home.html").read_text()
LLMS = (ROOT / "demo" / "llms.txt").read_text()

BY = {
    "podcast": {"count": 21, "hours": 73},
    "mcg": {"count": 668, "hours": 1077.2,
            "first": "2025-08-16", "last": "2026-10-09"},
    "elon": {"count": 24, "hours": 51.6,
             "first": "2018-09-07", "last": "2026-09-15"},
    "tradfi": {"count": 45, "hours": 47.9,
               "first": "2014-11-26", "last": "2026-09-25"},
    "threadguy": {"count": 621, "hours": 795.0,
                  "first": "2023-12-16", "last": "2026-10-09"},
}


def test_the_total_is_the_sum_of_what_each_archive_says_it_holds():
    sizes = total_sizes(BY)
    assert sizes["recordings"] == 21 + 668 + 24 + 45 + 621
    assert sizes["hours"] == round(73 + 1077.2 + 51.6 + 47.9 + 795.0)
    assert (sizes["archives"], sizes["first"], sizes["last"]) == (5, "2014", "2026")


def test_llms_txt_is_filled_in_from_the_archives():
    out = render_llms(LLMS, total_sizes(BY))
    assert "Total: 1,379 recordings, 2,045 hours." in out
    assert "- MCG Live: 668 episodes, 1,077.2 hours" in out
    assert "to 9 October 2026." in out
    assert "- ThreadGuy: 621 recordings, 795 hours" in out
    assert "{{" not in out and "}}" not in out


def test_llms_txt_without_a_count_says_what_it_was_written_with():
    """The lookup failing must not put a placeholder in front of a reader."""
    out = render_llms(LLMS, None)
    assert "{{" not in out and "}}" not in out
    assert re.search(r"Total: [\d,]+ recordings, [\d,]+ hours\.", out)
    half = render_llms(LLMS, {"hours": 2045, "by": {}})
    assert "{{" not in half
    assert "A search engine over 2,045 hours" in half


def test_every_figure_llms_txt_asks_for_is_one_the_archives_report():
    sizes = total_sizes(BY)
    for path, _fallback in _LLMS_FIGURE.findall(LLMS):
        value: object = sizes
        for part in path.split("."):
            assert isinstance(value, dict) and part in value, path
            value = value[part]


def test_no_total_in_llms_txt_is_typed_in():
    for line in LLMS.splitlines():
        if line.startswith(("Total:", "> A search engine over", "- MCG Live:",
                            "- ThreadGuy:", "- Elon Musk:", "- The Record:",
                            "- Market Bubble:")):
            assert "{{" in line, line


def test_the_front_door_asks_the_archives_how_big_they_are():
    assert 'fetch("/v1/archives")' in HOME
    assert "1,967" not in HOME and "1,328" not in HOME
    for key in ("sc-hours", "sc-recs", "sc-span"):
        assert f'id="{key}"' in HOME
    # Each card's sentence takes its count from the same answer.
    assert HOME.count('meta:"{n} ') == 5

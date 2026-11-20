"""The MCG page is laid out like ThreadGuy's and rasmr's.

Until 20 November 2026 it had a layout of its own: three figures, a
headline over a drawing, a worked example, and a shelf of fold-out rows.
Next to the other two it read as an older page. It is now ThreadGuy's
page in MCG's colours, on MCG's archive, and these hold it there.
"""

import asyncio
import re
from pathlib import Path

from app import main

DEMO = Path(__file__).resolve().parent.parent / "demo"
MCG = (DEMO / "mcg.html").read_text()
THREADGUY = (DEMO / "threadguy.html").read_text()


def script(html: str) -> str:
    """The page's own script, the last one in the file."""
    return html.rsplit("<script>", 1)[1].split("</script>", 1)[0]


def body(html: str) -> str:
    return html.split("<body>", 1)[1].split("<script src=\"/demo/nav.js\">", 1)[0]


def test_it_has_the_same_parts_in_the_same_order():
    parts = ['<div class="strip rise" id="strip"', '<section class="hero">',
             '<aside class="latest rise"', 'id="days"', '<section id="out"',
             'data-moments=', '<section class="sect rise"', 'id="shelf"', "<footer>"]
    for html in (MCG, THREADGUY):
        found = [body(html).index(part) for part in parts]
        assert found == sorted(found)
    assert body(MCG).count("<div><span>") == 4            # the four figures


def test_nothing_of_the_old_layout_is_left():
    for gone in ('class="readout"', 'class="gem"', 'class="eg"', 'class="seed"',
                 "details.rec", 'class="prompt"', "citeTimestamps", "/demo/ui.css"):
        assert gone not in MCG, gone


def test_it_asks_mcgs_archive_and_no_other():
    asked = re.findall(r'"/v1/(\w+)/', script(MCG))
    assert asked and set(asked) == {"mcg"}
    assert "threadguy" not in script(MCG).lower().replace("threadguy pages", "")
    # The same calls ThreadGuy's page makes, each to this archive.
    theirs = re.findall(r'"/v1/threadguy/([\w/]*)"', script(THREADGUY))
    ours = re.findall(r'"/v1/mcg/([\w/]*)"', script(MCG))
    assert ours == theirs and len(ours) == 7


def test_it_runs_the_same_script_as_threadguys_page():
    """One hardened script in two rooms. A fix made in one and not the
    other shows up here as a function one page has and the other lacks."""
    def functions(js: str) -> list[str]:
        return re.findall(r"function (\w+)\(", js)
    assert functions(script(MCG)) == functions(script(THREADGUY))
    assert len(functions(script(MCG))) > 25


def test_the_colours_are_mcgs_and_none_are_threadguys():
    css = MCG.split("</style>", 1)[0]
    assert "--mint:#6ecf9e" in css and "--void:#080b09" in css
    for theirs in ("--sky", "#6aa9ff", "106,169,255", "#060c1a", "#0b1530"):
        assert theirs not in MCG, theirs


def test_what_it_suggests_asking_is_about_projects():
    tries = re.search(r"var TRIES = \[(.*?)\];", script(MCG), re.S).group(1)
    asked = re.findall(r'"([^"]+)"', tries)
    assert len(asked) == 4 and all(q.endswith("?") for q in asked)
    assert "ThreadGuy" not in tries


def test_the_shelf_is_told_which_rows_are_streams():
    """ThreadGuy's shelf labels each row interview or stream. MCG's list
    did not send the field, so every label would have been blank."""
    rows = asyncio.run(main.mcg_episodes())
    assert rows and {row["format"] for row in rows} == {"interview", "stream"}
    assert [r["published_at"] for r in rows] == sorted(
        (r["published_at"] for r in rows), reverse=True)
    assert "e.format" in script(MCG)


def test_a_row_with_notes_opens_them_under_the_player():
    notes = main._mcg_summaries()
    shown = {row["episode_id"] for row in asyncio.run(main.mcg_episodes())}
    assert len(set(notes) & shown) >= 400
    assert 'fetch("/v1/mcg/summaries")' in script(MCG)
    assert "NOTES[e.episode_id]" in script(MCG)


def test_the_corner_and_the_foot_name_this_archive():
    assert '<a class="mark" href="/demo/mcg.html"><b>The MCG Index</b><span>archive</span></a>' in MCG
    foot = MCG.split("<footer>", 1)[1].split("</footer>", 1)[0]
    assert "not affiliated with MCG" in foot and "not financial advice" in foot
    assert 'href="https://x.com/Lexx_eth"' in foot
    for link in ('href="/mcg/assets"', 'href="/home"', 'href="/threadguy"', 'href="/rasmr"'):
        assert link in foot, link
    assert 'href="/mcg"' not in foot                       # not a link to itself

"""Times inside an answer become links only when a passage covers them.

The pages printed "[29:23]" and "[2026-05-23 at 8:16]" as plain text, so
an answer's citations could not be followed from the answer itself. The
cases here come from a real ThreadGuy answer about Hyperliquid, where a
bare 8:16 was covered by two different recordings: the May 23 stream the
answer named, and a hot seat episode it did not.

demo/cite.js is run under node, the same file the browser loads.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.schemas import PodcastHit  # noqa: E402

CITE = ROOT / "demo" / "cite.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def hit(vid, start, date, end=None, title="t"):
    return {"episode_id": vid, "title": title, "start_seconds": start,
            "deep_link": f"https://www.youtube.com/watch?v={vid}&t={int(start)}s",
            "published_at": date, "end_seconds": end}


HYPE = hit("JR4M9v9_2zE", 1752.56, "2024-12-13", end=1790, title="Hyperliquid")
HYPE_LATER = hit("JR4M9v9_2zE", 2492.18, "2024-12-13", end=2560, title="Hyperliquid")
SWEETGREEN = hit("QH2d-awq0No", 432.0, "2026-05-23", end=520, title="Sweetgreen")
HOT_SEAT = hit("nbQ_Cl4UmRc", 460.41, "2025-09-28", end=530, title="Hot seat")


def resolve(answer, hits):
    """[(stamp, video id, seconds)] for every time that became a link, and
    the text the reader sees."""
    script = (
        "const C=require(process.argv[1]);"
        "const [a,h]=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "const t=C.find(a,h);"
        "console.log(JSON.stringify({links:t.filter(x=>x.stamp).map("
        "x=>[x.stamp,x.hit.episode_id,x.sec,x.href]),"
        "text:t.map(x=>x.stamp||x.text).join(''),"
        "order:C.order(h,t).map(x=>x.title)}))")
    out = subprocess.run([NODE, "-e", script, str(CITE)],
                         input=json.dumps([answer, hits]),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_the_named_date_decides_between_two_recordings_at_the_same_time():
    answer = ("though he acknowledges it faces regulatory hurdles that could "
              "create volatility [2026-05-23 at 8:16].")
    got = resolve(answer, [HOT_SEAT, SWEETGREEN])
    assert [l[:3] for l in got["links"]] == [["8:16", "QH2d-awq0No", 496]]
    # the brackets go, and the date reads as a date
    assert got["text"].endswith("volatility May 23, 2026 at 8:16.")


@needs_node
def test_a_date_written_earlier_in_the_sentence_carries_to_the_time():
    answer = ("At [29:23] to [29:25] in the December 13, 2024 episode, he calls "
              "himself a degenerate.\n\nMore recently [8:16] he said more.")
    got = resolve(answer, [HOT_SEAT, SWEETGREEN, HYPE])
    assert [l[:2] for l in got["links"]][:2] == [["29:23", "JR4M9v9_2zE"],
                                                 ["29:25", "JR4M9v9_2zE"]]
    assert got["text"].startswith("At 29:23 to 29:25 in the December 13")


@needs_node
def test_a_link_lands_on_the_cited_second_not_the_passage_start():
    got = resolve("noting at [42:06] that it does $10 billion", [HYPE, HYPE_LATER])
    stamp, vid, sec, href = got["links"][0]
    assert (vid, sec) == ("JR4M9v9_2zE", 2526)
    assert href == "https://www.youtube.com/watch?v=JR4M9v9_2zE&t=2526s"


@needs_node
def test_a_time_no_passage_covers_stays_plain_text():
    answer = "he said it at [55:00] and again at 1:12:09."
    got = resolve(answer, [HYPE, SWEETGREEN])
    assert got["links"] == []
    assert got["text"] == answer


@needs_node
def test_clock_times_prices_and_ratios_are_not_citations():
    # 8:00 would be covered by SWEETGREEN (7:12 to 8:40) if it were a time
    # in the recording. It is a time of day.
    answer = ("the stream starts at 8:00 AM, or 8:00 pm, the price was $8:10 "
              "and the split was 10:1.")
    got = resolve(answer, [SWEETGREEN])
    assert got["links"] == []


@needs_node
def test_without_per_line_stamps_a_time_far_past_the_start_is_not_linked():
    old = dict(SWEETGREEN, end_seconds=None)
    got = resolve("at 7:30 and at 30:00", [old])
    assert [l[0] for l in got["links"]] == ["7:30"]


@needs_node
def test_cited_passages_come_first_so_their_cards_are_on_the_page():
    ranked = [hit(f"vid{i:08d}", 9000 + i, "2026-01-01", end=9001 + i, title=f"r{i}")
              for i in range(6)] + [SWEETGREEN]
    got = resolve("the thesis, at [12:10]", ranked)
    assert got["links"] == []  # 12:10 is past the passage: not covered
    got = resolve("the thesis, at [7:40]", ranked)
    assert got["order"][0] == "Sweetgreen"
    assert got["order"][1:] == [f"r{i}" for i in range(6)]


def test_hits_tell_the_page_where_each_passage_ends():
    h = PodcastHit(episode_id="e", title="t", start_seconds=60, timestamp="1:00",
                   deep_link="x", text="a", score=1,
                   text_ts="[1:00] a\n[1:31] b\n[1:02:05] c")
    data = h.model_dump()
    assert data["end_seconds"] == 3725.0
    assert "text_ts" not in data  # still not sent: it doubles the payload
    bare = PodcastHit(episode_id="e", title="t", start_seconds=60,
                      timestamp="1:00", deep_link="x", text="a", score=1)
    assert bare.model_dump()["end_seconds"] is None


@pytest.mark.parametrize("page", ["home.html", "threadguy.html"])
def test_both_answer_pages_load_the_resolver_and_use_it(page):
    html = (ROOT / "demo" / page).read_text()
    assert '<script src="/demo/cite.js"></script>' in html
    assert html.index("/demo/cite.js") < html.index("Cite.find(")
    assert "Cite.write(" in html and "Cite.order(" in html
    assert "function typeOut" not in html


@needs_node
def test_a_long_bracketed_citation_loses_its_brackets_too():
    # Verbatim shape from a live answer: range, quoted title, date.
    answer = ('At [27:50-28:03, "How Hyperliquid Revolutionized Crypto..." '
              'episode, Dec 13, 2024], he explains why.')
    early = hit("JR4M9v9_2zE", 1601.12, "2024-12-13", end=1700, title="Hyperliquid")
    got = resolve(answer, [early])
    assert [l[0] for l in got["links"]] == ["27:50", "28:03"]
    assert "[" not in got["text"] and "]" not in got["text"]


@pytest.mark.parametrize("page", ["home.html", "threadguy.html"])
def test_a_cited_moment_plays_full_width_right_under_the_answer(page):
    """As the Market Bubble page does. It played inside a card further
    down at part width, which read as a smaller, lesser player."""
    html = (ROOT / "demo" / page).read_text()
    body = html.split("<body")[1]
    assert body.index('id="answer"') < body.index('id="citeplay"') < body.index('id="hits"')
    write = html.split("Cite.write(")[1].split("}, document.hidden);")[0]
    assert "citeplay" in write or "playCite(" in write


@needs_node
def test_a_hostile_link_in_a_passage_never_becomes_a_citation():
    bad = dict(SWEETGREEN, deep_link="javascript:alert(1)//watch?v=QH2d-awq0No")
    data = dict(SWEETGREEN, deep_link="data:text/html,<script>x</script>")
    got = resolve("the thesis, at [7:40]", [bad, data])
    assert got["links"] == []


@needs_node
def test_a_citation_whose_title_has_brackets_loses_its_own_brackets():
    # Verbatim shape from a live answer about Tesla.
    answer = ('he said [15:33, "This FOMC Changes Everything... [Stream Recap]", '
              '2026-05-20] that it was over.')
    fomc = hit("FOMCFOMCFOM", 746.25, "2026-05-20", end=907,
               title="This FOMC Changes Everything... [Stream Recap]")
    got = resolve(answer, [fomc])
    assert [l[0] for l in got["links"]] == ["15:33"]
    assert got["text"] == ('he said 15:33, "This FOMC Changes Everything... '
                           '[Stream Recap]", May 20, 2026 that it was over.')


def render(answer, hits):
    """What write() would put on the page, as (kind, text) pairs."""
    script = (
        "const C=require(process.argv[1]);"
        "const [a,h]=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "console.log(JSON.stringify(C.find(a,h).map(t=>"
        "[t.stamp?'link':(t.bold?'bold':'text'),t.stamp||t.text])))")
    out = subprocess.run([NODE, "-e", script, str(CITE)],
                         input=json.dumps([answer, hits]),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_markdown_in_an_answer_renders_instead_of_printing_asterisks():
    # Verbatim shape from a live ThreadGuy answer.
    answer = ("Here's what he thinks:\n\n**On the product itself:** he calls it "
              "the best exchange at [23:41].\n\n- a list item\n## A heading")
    got = render(answer, [hit("3TtLaaHnfTY", 1401.3, "2026-05-30", end=1500)])
    flat = "".join(t for _, t in got)
    assert "**" not in flat and "## " not in flat
    assert ["bold", "On the product itself:"] in got
    assert ["link", "23:41"] in got
    assert "\n\u2022 a list item\nA heading" in flat


@needs_node
def test_an_unpaired_bold_marker_is_dropped_not_left_dangling():
    got = render("he said **it was over.", [])
    assert got == [["text", "he said it was over."]]


def test_every_player_runs_full_width_with_no_side_rail():
    """Tapping a time in an answer opens a full-width player, and the
    stages, shelf rows and question cards opened smaller ones with a
    caption rail beside them. They all match now."""
    tg = (ROOT / "demo" / "threadguy.html").read_text()
    home = (ROOT / "demo" / "home.html").read_text()
    for css, sel in ((tg, "  .tgstage{"), (tg, "  .pw{"),
                     (home, "  .askstage{"), (home, "  .stage{")):
        rule = css.split(sel)[1].split("}")[0]
        assert "grid-template-columns" not in rule, sel
    assert '<div class="cap">' not in tg
    assert '<div class="cap"><div class="qq">' not in home


def test_the_ticker_player_is_under_the_one_video_rule():
    home = (ROOT / "demo" / "home.html").read_text()
    stop = home.split("function stopPlaying(keepTicker){")[1].split("\n  }\n")[0]
    assert 'getElementById("stage")' in stop and "!keepTicker" in stop
    assert "stopPlaying(true);" in home

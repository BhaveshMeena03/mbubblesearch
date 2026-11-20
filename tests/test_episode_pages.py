"""A page per episode: what gets one, what its times link to, and that
nothing in a summary can become markup."""

import gzip
import json
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app import episode_pages as ep
from app import main, seo
from app import traffic as tr

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "demo" / "episode.html").read_text()
LIST = (ROOT / "demo" / "episodes.html").read_text()
SEEKS = re.compile(r"https://x\.com/i/broadcasts/[A-Za-z0-9]{6,20}\?t=\d{1,6}"
                   r"|https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&amp;t=\d{1,6}s")

EP24 = "x-2115904746355851479"
PLAYER = "https://x.com/i/broadcasts/1dxYlaZbXWQJX"
SUMMARY = """# Market Bubble Episode 24 — Summary

**TL;DR** — The show is **blindsided live** by the Binance hack.

**Topics**

- [0:00:03] Show opens on a red day.
- **[0:04:31]** Ansem lays out a bearish thesis.
- **[0:10:00]–[0:14:00]** Live commentary as the news lands.
- [2:30:00–2:45:00] The hacker sells.
- A bullet with no time in front of it.

**Notable moments**
- [1:35:20] Ansem: "I think I want to buy Zcash at like 750, bro"
A line the reader does not know.
*This summary is not financial advice.*
"""
ROW = {"episode_id": EP24, "platform": "other", "published_at": "2026-10-29",
       "title": "Market Bubble Episode 24 w/ @TraderMayne",
       "url": "https://x.com/MarketBubble/status/2115904746355851479", "summary": SUMMARY}
MCG_ROW = {"id": "dQw4w9WgXcQ", "title": "Greenwood, a neobank on Robinhood Chain",
           "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
           "published_at": "2026-11-15", "seconds": 2475, "format": "interview"}
NOTE = {"tldr": "Greenwood is a self-custody neobank.", "model": "claude-sonnet-5",
        "topics": [{"t": 112, "text": "What Greenwood is."},
                   {"t": 1090, "text": "Cashback paid in tokenized stocks."}]}
PAIR = {"broadcast": "https://x.com/i/broadcasts/2braplERJJLqda", "offset": -2}


# --- ids and times -----------------------------------------------------------------

@pytest.mark.parametrize("value", [EP24, "dQw4w9WgXcQ", "-uarB5g12aY", "_mLaSJLAPJs"])
def test_an_id_is_a_youtube_id_or_a_broadcasts(value):
    assert ep.is_id(value)


@pytest.mark.parametrize("value", ["", "x-12", "../etc/passwd", "dQw4w9WgXc", "dQw4w9WgXcQQ",
                                   "<script>aaa", "dQw4w9W XcQ", "x-21159047463558514790000",
                                   None, 42, "dQw4w9WgXcQ/"])
def test_anything_else_is_not_an_id(value):
    assert not ep.is_id(value)


def test_times_are_written_the_way_a_player_shows_them():
    assert ep.clock(5720) == "1:35:20" and ep.clock(320) == "5:20" and ep.clock(3) == "0:03"
    assert ep.length(11520) == "3 hr 12 min" and ep.length(2475) == "41 min"
    assert ep.length(3600) == "1 hr 00 min"
    assert ep.length(None) == "" and ep.length(30) == "" and ep.length("long") == ""


# --- where a moment plays ----------------------------------------------------------

def test_a_broadcast_opens_in_its_own_player_at_the_second():
    assert ep.moment(EP24, 5720, player=PLAYER) == f"{PLAYER}?t=5720"


def test_a_show_that_also_went_out_on_x_opens_there_on_xs_clock():
    assert ep.moment("dQw4w9WgXcQ", 112, pair=PAIR) == "https://x.com/i/broadcasts/2braplERJJLqda?t=110"


def test_where_the_clocks_are_not_known_to_agree_it_opens_on_youtube():
    apart = {"broadcast": PAIR["broadcast"],
             "sections": [{"from": 0, "to": 3090, "offset": 0},
                          {"from": 3155, "to": None, "offset": -5}]}
    youtube = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t={}s"
    assert ep.moment("dQw4w9WgXcQ", 100, pair=apart).endswith("?t=100")
    assert ep.moment("dQw4w9WgXcQ", 3100, pair=apart) == youtube.format(3100)   # the gap
    assert ep.moment("dQw4w9WgXcQ", 4000, pair=apart).endswith("?t=3995")
    # Said before the stream on X began.
    assert ep.moment("dQw4w9WgXcQ", 1, pair=PAIR) == youtube.format(1)
    assert ep.moment("dQw4w9WgXcQ", 112) == youtube.format(112)
    assert ep.moment("dQw4w9WgXcQ", 112, pair={"offset": -2}) == youtube.format(112)


@pytest.mark.parametrize("seconds", [-1, 86400, None, "112", True, float("nan")])
def test_a_time_that_is_not_a_time_gets_no_link(seconds):
    assert ep.moment("dQw4w9WgXcQ", seconds, pair=PAIR) == ""


def test_a_link_is_only_ever_a_player_at_a_second():
    assert ep.moment(EP24, 10, player="javascript:alert(1)") == ""
    assert ep.moment(EP24, 10, player="https://evil.example/i/broadcasts/abcdefgh") == ""
    assert ep.moment(EP24, 10) == ""                    # a broadcast with no player on file
    assert ep.moment("not an id", 10) == ""
    assert ep.recording("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert ep.recording("https://x.com/MarketBubble/status/2115904746355851479")
    assert ep.recording("javascript:alert(1)") == "" and ep.recording(None) == ""
    assert ep.recording('https://x.com/a"><script>') == ""


# --- what can be played in the page ------------------------------------------------

def upload(episode_id: str, second: int):
    """A stand-in for the map of a broadcast onto its YouTube upload: the
    upload starts 100 seconds in and was cut after the first hour."""
    return ("V3RNvh6GbEs", second - 100) if 100 <= second < 3600 else None


def test_a_recording_indexed_from_youtube_is_its_own_copy():
    assert ep.on_youtube("dQw4w9WgXcQ", 112) == ("dQw4w9WgXcQ", 112)
    assert ep.on_youtube("dQw4w9WgXcQ", 112, upload) == ("dQw4w9WgXcQ", 112)


def test_a_broadcast_plays_where_its_upload_has_the_moment_and_nowhere_else():
    assert ep.on_youtube(EP24, 271, upload) == ("V3RNvh6GbEs", 171)
    assert ep.on_youtube(EP24, 50, upload) is None           # before the upload starts
    assert ep.on_youtube(EP24, 5720, upload) is None         # cut from the upload
    assert ep.on_youtube(EP24, 271) is None                  # nobody to ask


@pytest.mark.parametrize("answer", [("not an id", 5), ("V3RNvh6GbEs", -1), ("V3RNvh6GbEs", "5"),
                                    ("V3RNvh6GbEs",), "V3RNvh6GbEs", 5, (None, None)])
def test_a_bad_answer_from_the_map_costs_one_link_and_not_the_page(answer):
    assert ep.on_youtube(EP24, 271, lambda *_: answer) is None

    def broken(*_):
        raise IndexError("a row with two fields")
    assert ep.on_youtube(EP24, 271, broken) is None


@pytest.mark.parametrize("seconds", [-1, 86400, None, "112", True])
def test_a_time_that_is_not_a_time_plays_nothing(seconds):
    assert ep.on_youtube("dQw4w9WgXcQ", seconds) is None


def test_a_time_on_youtube_plays_in_the_page_and_one_that_is_not_goes_to_x():
    made = ep.from_summary(ROW, player=PLAYER, youtube=upload)
    out = ep.render_page(PAGE, made)
    # 4:31 is in the upload: played here, and a link to YouTube without script.
    assert ('<a class="at" href="https://www.youtube.com/watch?v=V3RNvh6GbEs&amp;t=171s" '
            'data-yt="V3RNvh6GbEs" data-at="171" target="_blank" rel="noopener" '
            'aria-label="Play from 4:31">4:31</a>') in out
    # 1:35:20 was cut from it: the broadcast on X, marked as a way out.
    assert (f'<a class="at out" href="{PLAYER}?t=5720" target="_blank" rel="noopener" '
            'aria-label="Watch on X from 1:35:20">1:35:20</a>') in out
    # Of the five times, two are in the stand-in upload (4:31 and 10:00).
    assert out.count("data-yt=") == 2 and out.count('class="at out"') == 3


def test_his_own_video_and_an_unpinned_note_play_nothing():
    own = ep.render_page(PAGE, ep.from_summary(dict(ROW, show="Ansem on X"),
                                               player=PLAYER, youtube=upload))
    assert "data-yt" not in own and 'class="at off"' in own
    old = ep.render_page(PAGE, ep.from_note("mcg", MCG_ROW, NOTE, PAIR))
    assert "data-yt" not in old and "<a class=\"at" not in old


def test_a_pinned_note_plays_every_topic_from_its_own_recording():
    out = ep.render_page(PAGE, ep.from_note("mcg", MCG_ROW, dict(NOTE, pinned=True), PAIR))
    assert 'data-yt="dQw4w9WgXcQ" data-at="112"' in out
    assert 'data-yt="dQw4w9WgXcQ" data-at="1090"' in out
    assert 'href="https://www.youtube.com/watch?v=dQw4w9WgXcQ&amp;t=112s"' in out


def test_a_call_made_on_the_show_plays_in_the_page_too():
    calls = [{"episode_id": EP24, "speaker": "Ansem", "asset": "Zcash", "timestamp": "4:31",
              "start_seconds": 271.4, "quote": "a quote", "link": f"{PLAYER}?t=271"},
             {"episode_id": EP24, "speaker": "Ansem", "asset": "Bitcoin", "timestamp": "1:35:20",
              "start_seconds": 5720.0, "quote": "another", "link": f"{PLAYER}?t=5720"}]
    made = ep.from_summary(ROW, player=PLAYER, calls=calls, youtube=upload)
    assert [c["yt"] for c in made["calls"]] == [("V3RNvh6GbEs", 171), None]
    out = ep.render_page(PAGE, made)
    assert 'aria-label="Play from 4:31">4:31</a><p><b>Ansem on Zcash:</b>' in out
    assert f'<a class="at out" href="{PLAYER}?t=5720"' in out


# --- reading a summary -------------------------------------------------------------

def test_a_summary_is_read_whatever_way_it_was_laid_out():
    tldr, blocks = ep.read_summary(SUMMARY)
    assert tldr == "The show is **blindsided live** by the Binance hack."
    assert [(b["kind"], b.get("t"), b.get("to")) for b in blocks] == [
        ("h", None, None), ("li", 3, None), ("li", 271, None), ("li", 600, 840),
        ("li", 9000, 9900), ("li", None, None),
        ("h", None, None), ("li", 5720, None), ("p", None, None)]
    assert blocks[0]["text"] == "Topics" and blocks[6]["text"] == "Notable moments"
    assert blocks[2]["text"] == "Ansem lays out a bearish thesis."
    assert blocks[3]["text"] == "Live commentary as the news lands."
    assert blocks[8]["text"] == "A line the reader does not know."


def test_the_models_own_aside_and_the_repeated_title_are_left_out():
    _, blocks = ep.read_summary(SUMMARY)
    said = " ".join(b["text"] for b in blocks)
    assert "financial advice" not in said and "Summary" not in said


@pytest.mark.parametrize("opening", ["**TL;DR** — It rained.", "**TL;DR:** It rained.",
                                     "**TL;DR**: It rained.", "**TL;DR**\nIt rained.",
                                     "**TL;DR:**\n\nIt rained."])
def test_the_tldr_is_found_however_it_is_marked(opening):
    assert ep.read_summary(opening + "\n\n**Topics**\n- [0:01] One.")[0] == "It rained."


@pytest.mark.parametrize("text", ["", None, "   \n\n  "])
def test_nothing_in_is_nothing_out(text):
    assert ep.read_summary(text) == ("", [])


def test_a_time_that_cannot_be_one_is_not_linked():
    _, blocks = ep.read_summary("**Topics**\n- [0:75:00] An impossible minute.")
    assert blocks[1]["t"] is None and blocks[1]["text"] == "An impossible minute."


# --- one episode -------------------------------------------------------------------

def test_a_market_bubble_show_links_each_time_into_its_own_broadcast():
    made = ep.from_summary(ROW, player=PLAYER)
    assert (made["archive"], made["id"], made["show"]) == ("podcast", EP24, "Market Bubble")
    assert made["date"] == "2026-10-29" and made["watch"] == ROW["url"]
    links = [b["link"] for b in made["blocks"] if b["kind"] == "li"]
    assert links == [f"{PLAYER}?t=3", f"{PLAYER}?t=271", f"{PLAYER}?t=600",
                     f"{PLAYER}?t=9000", "", f"{PLAYER}?t=5720"]


def test_one_of_his_own_videos_keeps_its_times_as_text():
    """An uploaded video on X does not open at a second."""
    made = ep.from_summary(dict(ROW, show="Ansem on X"), player=PLAYER)
    assert made["show"] == "Ansem on X"
    assert all(not b["link"] for b in made["blocks"])
    assert any(b.get("t") == 5720 for b in made["blocks"])


def test_an_appearance_on_another_show_opens_on_youtube():
    row = dict(ROW, episode_id="dQw4w9WgXcQ", show="Unchained", platform="youtube",
               url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    made = ep.from_summary(row, player=PLAYER)
    assert made["show"] == "Unchained"
    assert made["blocks"][1]["link"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3s"


def test_a_show_carries_only_the_calls_made_on_it():
    calls = [{"episode_id": EP24, "speaker": "Ansem", "asset": "Zcash"},
             {"episode_id": "x-2118457515969032365", "speaker": "Ansem", "asset": "Bitcoin"}]
    assert [c["asset"] for c in ep.from_summary(ROW, calls=calls)["calls"]] == ["Zcash"]


@pytest.mark.parametrize("row", [dict(ROW, summary=""), dict(ROW, episode_id="../x"),
                                 dict(ROW, title="  "), dict(ROW, summary=None)])
def test_a_row_with_nothing_to_say_is_not_a_page(row):
    assert ep.from_summary(row) is None


def test_a_note_that_is_not_pinned_lists_its_topics_and_links_none():
    """Written before 28 October 2026, its times could be half an hour
    late, and a link opens whatever minute it is given."""
    made = ep.from_note("mcg", MCG_ROW, NOTE, PAIR)
    topics = [b for b in made["blocks"] if b["kind"] == "li"]
    assert [b["text"] for b in topics] == ["What Greenwood is.", "Cashback paid in tokenized stocks."]
    assert all(b["t"] is None and b["link"] == "" for b in topics)
    assert made["tldr"] == NOTE["tldr"] and made["seconds"] == 2475


def test_a_pinned_note_links_every_topic():
    made = ep.from_note("mcg", MCG_ROW, dict(NOTE, pinned=True), PAIR)
    assert [b["link"] for b in made["blocks"] if b["kind"] == "li"] == [
        "https://x.com/i/broadcasts/2braplERJJLqda?t=110",
        "https://x.com/i/broadcasts/2braplERJJLqda?t=1088"]
    alone = ep.from_note("threadguy", MCG_ROW, dict(NOTE, pinned=True))
    assert alone["blocks"][1]["link"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=112s"
    assert alone["show"] == "ThreadGuy"


@pytest.mark.parametrize("pinned", ["yes", 1, None, False])
def test_only_the_word_true_pins_a_note(pinned):
    made = ep.from_note("mcg", MCG_ROW, dict(NOTE, pinned=pinned), PAIR)
    assert all(not b.get("link") for b in made["blocks"])


@pytest.mark.parametrize("row, note", [(MCG_ROW, None), (MCG_ROW, {}), (MCG_ROW, dict(NOTE, tldr="")),
                                       (MCG_ROW, dict(NOTE, tldr=None)), (dict(MCG_ROW, id="bad id"), NOTE),
                                       (dict(MCG_ROW, title=""), NOTE), (MCG_ROW, "a note")])
def test_a_recording_with_no_note_is_not_a_page(row, note):
    assert ep.from_note("mcg", row, note) is None
    assert ep.from_note("somewhere", MCG_ROW, NOTE) is None


def test_the_shelf_runs_newest_first_and_each_page_knows_its_neighbours():
    a = ep.from_note("mcg", dict(MCG_ROW, id="aaaaaaaaaaa", published_at="2026-11-01"), NOTE)
    b = ep.from_note("mcg", dict(MCG_ROW, id="bbbbbbbbbbb", published_at="2026-11-10"), NOTE)
    c = ep.from_note("mcg", dict(MCG_ROW, id="ccccccccccc", published_at="2026-11-15"), NOTE)
    other = ep.from_summary(ROW)
    shelf = [a, other, c, b]
    assert [e["id"] for e in ep.in_order(shelf)] == ["ccccccccccc", "bbbbbbbbbbb", "aaaaaaaaaaa", EP24]
    assert [e and e["id"] for e in ep.neighbours(shelf, "bbbbbbbbbbb")] == ["ccccccccccc", "aaaaaaaaaaa"]
    assert ep.neighbours(shelf, "ccccccccccc")[0] is None
    assert ep.neighbours(shelf, "aaaaaaaaaaa")[1] is None
    assert ep.neighbours(shelf, EP24) == (None, None)          # alone in its archive
    assert ep.neighbours(shelf, "zzzzzzzzzzz") == (None, None)


# --- the page ----------------------------------------------------------------------

def test_the_page_says_what_the_summary_says_and_names_its_own_address():
    made = ep.from_summary(ROW, player=PLAYER, calls=[
        {"episode_id": EP24, "speaker": "Ansem", "asset": "Zcash", "timestamp": "1:35:20",
         "quote": "I think I want to buy Zcash at like 750, bro", "link": f"{PLAYER}?t=5720"}])
    out = ep.render_page(PAGE, made)
    assert "{{" not in out and "}}" not in out
    assert "<h1>Market Bubble Episode 24 w/ @TraderMayne</h1>" in out
    assert f'<link rel="canonical" href="{seo.SITE}/episodes/{EP24}">' in out
    assert f'<meta property="og:url" content="{seo.SITE}/episodes/{EP24}">' in out
    assert "The show is <b>blindsided live</b> by the Binance hack." in out
    assert '<meta name="description" content="The show is blindsided live by the Binance hack.">' in out
    assert f'href="{PLAYER}?t=271"' in out and ">4:31</a>" in out
    assert ">10:00 to 14:00</a>" in out
    assert "October 29, 2026" in out and "<b>Market Bubble</b>" in out
    assert "Calls made on this show" in out and "Ansem on Zcash:" in out
    assert 'href="/demo/podcast.html"' in out and "Ask Market Bubble anything" in out
    assert "Each time plays the show from that second." in out


def test_the_page_is_whole_without_its_one_script_and_is_not_hidden_from_search():
    """The script only keeps a reader on the page. Every time it would
    play is already a link to that second."""
    out = ep.render_page(PAGE, ep.from_summary(ROW, player=PLAYER, youtube=upload))
    assert re.findall(r"<script[^>]*>", out) == ['<script src="/demo/episode.js" defer>']
    assert 'name="robots"' not in out
    times = re.findall(r'<a class="at[^"]*"[^>]*>', out)
    assert len(times) == 5 and all('href="https://' in t for t in times)


def test_nothing_in_a_title_or_a_summary_becomes_markup():
    nasty = dict(ROW, title='<script>alert(1)</script> "quoted" & co',
                 summary='**TL;DR** — <img src=x onerror=alert(1)> "said"\n\n**Topics**\n'
                         '- [0:01] <b onclick="x()">bold</b> **<i>real</i>**\n')
    out = ep.render_page(PAGE, ep.from_summary(nasty, player=PLAYER))
    assert "<script>alert" not in out and "<img" not in out and "<b onclick" not in out
    assert "&lt;b onclick=&quot;x()&quot;&gt;bold&lt;/b&gt;" in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out
    assert "<b>&lt;i&gt;real&lt;/i&gt;</b>" in out


def test_a_page_with_no_times_drops_the_margin_they_would_sit_in():
    out = ep.render_page(PAGE, ep.from_note("mcg", MCG_ROW, NOTE, PAIR))
    assert 'class="notes plain"' in out
    assert "Each time opens the show" not in out
    assert "41 min" in out and "MCG Live" in out
    timed = ep.render_page(PAGE, ep.from_note("mcg", MCG_ROW, dict(NOTE, pinned=True), PAIR))
    assert 'class="notes"' in timed and ">1:52</a>" in timed


def test_every_link_out_opens_in_its_own_tab_without_handing_over_the_page():
    out = ep.render_page(PAGE, ep.from_summary(ROW, player=PLAYER))
    outward = re.findall(r'<a [^>]*href="https://[^"]+"[^>]*>', out)
    assert len(outward) >= 6
    assert all('target="_blank"' in a and 'rel="noopener"' in a for a in outward)


def test_the_page_points_at_the_one_after_and_the_one_before():
    here = ep.from_note("mcg", MCG_ROW, NOTE)
    newer = ep.from_note("mcg", dict(MCG_ROW, id="ccccccccccc", title="The newer one"), NOTE)
    older = ep.from_note("mcg", dict(MCG_ROW, id="aaaaaaaaaaa", title="The <older> one"), NOTE)
    out = ep.render_page(PAGE, here, newer, older)
    assert '<a class="step" href="/episodes/ccccccccccc"><span>Newer</span><b>The newer one</b></a>' in out
    assert 'href="/episodes/aaaaaaaaaaa"><span>Older</span><b>The &lt;older&gt; one</b>' in out


def test_a_long_summary_is_cut_on_a_word_for_the_search_result():
    long = dict(NOTE, tldr="word " * 100)
    out = ep.render_page(PAGE, ep.from_note("mcg", MCG_ROW, long))
    said = re.search(r'<meta name="description" content="([^"]*)">', out).group(1)
    assert len(said) <= 221 and said.endswith("word…")


def test_the_list_groups_by_archive_and_counts_what_it_lists():
    shelf = [ep.from_summary(ROW), ep.from_note("mcg", MCG_ROW, NOTE),
             ep.from_note("threadguy", dict(MCG_ROW, id="ttttttttttt", title="A <stream>"), NOTE)]
    out = ep.render_index(LIST, shelf)
    assert "{{" not in out and "<b>3 recordings</b>" in out
    assert out.index("Market Bubble <span>1</span>") < out.index("MCG Live <span>1</span>") \
        < out.index("ThreadGuy <span>1</span>")
    assert f'href="/episodes/{EP24}"' in out and "A &lt;stream&gt;" in out
    assert f'<link rel="canonical" href="{seo.SITE}/episodes">' in out
    assert "<script" not in out.lower()


# --- what ships --------------------------------------------------------------------

def notes(name: str) -> dict:
    return json.loads(gzip.decompress((ROOT / "data" / f"{name}_summaries.json.gz").read_bytes()))


def test_every_archive_with_summaries_has_its_pages():
    shelf = main._episode_catalog()
    count = {key: sum(1 for e in shelf if e["archive"] == key) for key in ep.ARCHIVES}
    assert count["podcast"] >= 40 and count["mcg"] >= 400 and count["threadguy"] >= 100
    assert len({e["id"] for e in shelf}) == len(shelf)
    assert shelf == ep.in_order(shelf)
    for e in shelf:
        assert ep.is_id(e["id"]) and e["title"] and e["tldr"] and e["watch"], e["id"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"]), e["id"]


def test_every_shipped_page_renders_and_links_only_to_a_player_at_a_second():
    shelf = main._episode_catalog()
    for e in shelf:
        newer, older = ep.neighbours(shelf, e["id"])
        out = ep.render_page(PAGE, e, newer, older)
        assert "{{" not in out, e["id"]
        assert re.findall(r"<script[^>]*>", out) == ['<script src="/demo/episode.js" defer>'], e["id"]
        for link in re.findall(r'<a class="at(?: out)?" href="([^"]+)"', out):
            assert SEEKS.fullmatch(link), (e["id"], link)
        for video, at in re.findall(r'data-yt="([^"]*)" data-at="([^"]*)"', out):
            assert re.fullmatch(r"[A-Za-z0-9_-]{11}", video) and at.isdigit(), e["id"]


def test_no_shipped_note_that_is_not_pinned_links_a_time():
    written = {"mcg": notes("mcg"), "threadguy": notes("threadguy")}
    linked = unlinked = 0
    for e in main._episode_catalog():
        if e["archive"] == "podcast":
            continue
        pinned = written[e["archive"]][e["id"]].get("pinned") is True
        has = any(b.get("link") for b in e["blocks"])
        assert has <= pinned, e["id"]
        linked += has
        unlinked += not has
    assert linked >= 80 and unlinked >= 400


def test_a_market_bubble_time_opens_that_shows_own_broadcast():
    players = json.loads((ROOT / "data" / "broadcast_links.json").read_text())
    seen = 0
    for e in main._episode_catalog():
        if e["archive"] != "podcast" or not e["id"].startswith("x-"):
            continue
        for b in e["blocks"]:
            if b.get("link"):
                assert b["link"] == f"{players[e['id']]}?t={b['t']}", e["id"]
                seen += 1
    assert seen >= 300


def test_most_shipped_moments_play_in_the_page_and_a_broadcasts_follow_its_upload():
    from app import youtube_map
    inline = out = 0
    for e in main._episode_catalog():
        for b in e["blocks"]:
            if b.get("t") is None:
                continue
            if e["id"].startswith("x-") and not ep.his_own(e["show"]):
                assert b["yt"] == ep.on_youtube(e["id"], b["t"], youtube_map.youtube_moment), e["id"]
            elif b.get("link"):
                assert b["yt"] == (e["id"], b["t"]), e["id"]
            inline += bool(b.get("yt"))
            out += bool(b.get("link")) and not b.get("yt")
    assert inline >= 1200 and out < inline / 10


SCRIPT = (ROOT / "demo" / "episode.js").read_text()


def test_the_player_script_leaves_a_link_a_link_when_it_should():
    """Read as written: a click asking for a new tab is not taken over,
    an id that is not an id is not played, and the frame is YouTube's
    cookie-less player and nothing else."""
    assert 'closest("a.at[data-yt]")' in SCRIPT
    assert "event.metaKey" in SCRIPT and "event.ctrlKey" in SCRIPT and "event.button !== 0" in SCRIPT
    assert "/^[A-Za-z0-9_-]{11}$/" in SCRIPT
    assert re.findall(r'"(https://[^"]+)"', SCRIPT) == ["https://www.youtube-nocookie.com/embed/"]
    assert "innerHTML" not in SCRIPT and "eval(" not in SCRIPT


# --- as served ---------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    async def sizes(request):
        return None

    monkeypatch.setattr(main, "TRAFFIC", tr.Traffic(index=None, salt="s", dimension=4))
    monkeypatch.setattr(main, "_archive_sizes", sizes)
    with TestClient(main.app) as c:
        yield c


def test_the_player_script_is_served_and_revalidated(client):
    r = client.get("/demo/episode.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    assert "x-robots-tag" not in r.headers


def test_an_episode_is_served_whole_at_its_own_address(client):
    r = client.get(f"/episodes/{EP24}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "x-robots-tag" not in r.headers
    assert "<h1>Market Bubble Episode 24" in r.text
    assert f'href="{PLAYER}?t=' in r.text
    assert f'<link rel="canonical" href="{seo.SITE}/episodes/{EP24}">' in r.text


def test_the_list_links_every_page_there_is(client):
    r = client.get("/episodes")
    assert r.status_code == 200
    linked = set(re.findall(r'href="/episodes/([^"]+)"', r.text))
    assert linked == {e["id"] for e in main._episode_catalog()}


@pytest.mark.parametrize("path", ["/episodes/zzzzzzzzzzz", "/episodes/x-1", "/episodes/%3Cscript%3E",
                                  "/episodes/dQw4w9WgXcQQQQQQ"])
def test_an_episode_with_no_page_is_a_small_404_that_points_at_the_list(client, path):
    """Not the list itself: it is a hundred kilobytes, and an address
    anybody can make up should not be able to ask for it."""
    r = client.get(path)
    assert r.status_code == 404
    assert "There is no episode at this address." in r.text
    assert '<a href="/episodes">See every episode</a>' in r.text
    assert len(r.text) < 6000 and 'class="row"' not in r.text
    assert "<script" not in r.text.lower() and "zzzz" not in r.text


def test_the_templates_are_not_served_as_pages_of_braces(client):
    for name in ("episode.html", "episodes.html"):
        r = client.get(f"/demo/{name}", follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == "/episodes"


def test_the_sitemap_lists_the_list_and_every_episode(client):
    root = ElementTree.fromstring(client.get("/sitemap.xml").text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    found = {u.findtext("s:loc", "", ns): u.findtext("s:lastmod", "", ns)
             for u in root.findall("s:url", ns)}
    assert f"{seo.SITE}/episodes" in found
    for e in main._episode_catalog():
        assert found[f"{seo.SITE}/episodes/{e['id']}"] == e["date"]
    assert len(found) == len(seo.PAGES) + 1 + len(main._episode_catalog())


def test_the_front_door_and_the_broadcast_page_link_the_list():
    assert '<a href="/episodes">Every episode</a>' in (ROOT / "demo" / "home.html").read_text()
    assert 'href="/episodes"' in (ROOT / "demo" / "podcast.html").read_text()


def test_every_episode_view_is_counted_in_one_row():
    """A day's pages are kept in one small record. Six hundred addresses
    in it, one per episode, would outgrow it."""
    t = tr.Traffic(index=None, salt="s", dimension=4)
    for path in (f"/episodes/{EP24}", "/episodes/dQw4w9WgXcQ", "/episodes/dQw4w9WgXcQ/", "/episodes"):
        t.page(path, "1.2.3.4", "Mozilla/5.0 Safari", None)
    t.page("/episodes/a/b", "1.2.3.4", "Mozilla/5.0 Safari", None)       # not a page
    assert dict(t._day()["pages"]) == {"/episodes/*": 3, "/episodes": 1}

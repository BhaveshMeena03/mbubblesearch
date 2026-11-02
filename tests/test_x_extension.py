"""The browser extension in extension/ and the three routes it calls.

The extension puts a button on X posts that are recordings in these
archives: the summary, an ask box, and times that move X's own player to
the second. Built 31 Oct 2026. What has to stay true:

  - a post gets its archive's episode and the right clock, from files the
    service ships, and a broken file costs only its own archive
  - every time shown converts to X's clock here, the one copy of that sum
  - a question about a post is answered from that recording only, with
    every limit any other question has, and the caller picks nothing but
    the post
  - the extension asks for no permission it does not use, talks to one
    origin, and never parses markup
  - a clip placed in the archive says where it is from; a time inside it
    plays in the clip, and the rest open the whole show, only ever at an
    address of one of three shapes
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import podcast, x_posts, youtube_map
from app.config import get_settings
from app.schemas import PodcastHit, PodcastSearchResponse

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"

MB_POST = "2116220650058846455"          # Ansem's gold video, on the MB shelf
EP24 = "x-2115904746355851479"           # Market Bubble Episode 24, on X
CLIP_POST = "2116428709150478840"        # Ansem's clip of it, from 1:39:53
MCG_POST = "2115115665753518109"
RASMR_PAIRED = "2090840111306936721"
RASMR_X_ONLY = "2109683904819150927"
TG_POST = "2116149081211048039"          # ThreadGuy's market open, 30 Oct
LINK_POST = "2116946451729432772"        # ours: Mando on ThreadGuy's stream, at 1:40:07 on X
MB_LINK_POST = "2116435942009430106"     # ours: Ansem on Episode 24, at 1:39:53
YT_MB_POST = "2103226841083564495"       # ours, before October: Episode 24's upload
YT_TG_POST = "2116000000000000020"       # a YouTube link to a show paired with X
YT_MCG_POST = "2116000000000000021"      # a YouTube link to a show never paired
YT_YOUTUBE_EP = "2116000000000000022"    # a Market Bubble episode held only from YouTube
TG_CLIP = "2116000000000000030"          # ThreadGuy's clip of his 30 Oct open, paired with X
MCG_CLIP = "2116000000000000031"         # MCG's clip of a show never on X


# --- the shelf -------------------------------------------------------------

@pytest.fixture
def files(tmp_path, monkeypatch):
    """A small, known set of the five files the shelf is built from."""
    with gzip.open(tmp_path / "summaries.json.gz", "wt") as fh:
        json.dump([{"episode_id": f"x-{MB_POST}", "title": "Ansem on X: gold",
                    "show": "Ansem on X", "published_at": "2026-10-30",
                    "summary": "**Topics**\n- [0:01:40] blow off top\n- [0:04:57] bottom"},
                   {"episode_id": "KcazozbZHd0", "title": "a YouTube one",
                    "summary": "x", "platform": "youtube",
                    "url": "https://www.youtube.com/watch?v=KcazozbZHd0"},
                   {"episode_id": EP24, "title": "Market Bubble Episode 24",
                    "published_at": "2026-10-29", "platform": "other",
                    "url": "https://x.com/MarketBubble/status/2115904746355851479",
                    "summary": "**Topics**\n- [0:04:31] macro\n- [1:40:05] the clip's moment\n"
                               "- [2:00:00] later, CPI at 8:30 ET"},
                   {"episode_id": "x-2115418459765113280", "title": "Q4 market thoughts",
                    "platform": "other", "summary": "- [0:10:37] AI stocks",
                    "url": "https://x.com/blknoiz06/status/2115418459765113280"},
                   {"episode_id": "x-2111111111111111111", "title": "a bad row",
                    "platform": "other", "url": "javascript:alert(1)//x.com/a/status/1"}], fh)
    (tmp_path / "broadcast_links.json").write_text(json.dumps(
        {"x-2115904746355851479": "https://x.com/i/broadcasts/1dxYlaZbXWQJX",
         "x-notdigits": "https://x.com/i/broadcasts/abc"}))
    (tmp_path / "mcg.json").write_text(json.dumps({
        "2NK_MBlFvtM": {"broadcast": "b", "post": MCG_POST,
                        "sections": [{"from": 0, "offset": -128, "to": 1418},
                                     {"from": 1961, "offset": -161, "to": None}]},
        "bad": {"broadcast": "b", "post": "../../etc/passwd", "offset": 0}}))
    (tmp_path / "rasmr.json").write_text(json.dumps({
        "2bev0UDp79Y": {"broadcast": "b", "post": RASMR_PAIRED, "offset": -15},
        "WlvYe5EdIJ0": {"broadcast": "b", "post": RASMR_X_ONLY, "offset": -40},
        "noclock0000": {"broadcast": "b", "post": "2099999999999999999"}}))
    (tmp_path / "rasmr_index.json").write_text(json.dumps([
        {"id": f"x-{RASMR_X_ONLY}", "title": "LIVE: Portfolio Update",
         "published_at": "2026-10-12", "seconds": 7539},
        {"id": "2bev0UDp79Y", "title": "a paired stream", "published_at": "2026-08-21"}]))
    (tmp_path / "mcg_index.json").write_text(json.dumps([
        {"id": "2NK_MBlFvtM", "title": "MCG Live: Binance", "published_at": "2026-10-27"},
        {"id": "pwhvqgCGV0w", "title": "This Trader Caught $ORBIO", "published_at": "2026-10-01"}]))
    (tmp_path / "x_clips.json").write_text(json.dumps({
        CLIP_POST: {"account": "blknoiz06", "archive": "podcast", "episode_id": EP24,
                    "start": 5993, "end": 6072, "before": "lab diamonds.",
                    "after": "How does MicroStrategy look?", "after_at": 6078},
        "2116000000000000001": {"account": "MarketBubble", "archive": "podcast",
                                "episode_id": "KcazozbZHd0",
                                "start": 60, "end": 90, "before": "", "after": "a",
                                "after_at": None},
        MB_POST: {"archive": "podcast", "episode_id": EP24, "start": 1, "end": 2},
        "2115461279163052437": {"account": "blknoiz06", "archive": "podcast",
                                "episode_id": "x-2115418459765113280",
                                "start": 1719, "end": 1805, "before": "b", "after": "a",
                                "after_at": 1809},
        "2116000000000000002": {"archive": "podcast", "episode_id": EP24, "start": "5993", "end": 6072},
        "2116000000000000003": {"archive": "podcast", "episode_id": EP24, "start": 90, "end": 60},
        "2116000000000000004": {"archive": "podcast", "episode_id": "../../etc", "start": 1, "end": 2},
        "2116000000000000005": {"archive": "elon", "episode_id": EP24, "start": 1, "end": 2},
        "2116000000000000006": {"archive": "podcast", "episode_id": EP24, "start": 1, "end": 2,
                                "after": {"not": "text"}},
        "2116000000000000007": {"archive": "podcast", "episode_id": EP24, "start": True, "end": 2},
        "2116000000000000008": {"archive": "podcast", "episode_id": EP24, "start": 1, "end": 2,
                                "after_at": -5},
        "not-a-post": {"archive": "podcast", "episode_id": EP24, "start": 1, "end": 2},
        "2116000000000000009": {"account": "someclipper", "archive": "podcast",
                                "episode_id": EP24, "start": 5993, "end": 6072},
        TG_CLIP: {"account": "notthreadguy", "archive": "threadguy", "episode_id": "EOkDv8qm8ik",
                  "start": 590, "end": 640, "before": "b", "after": "a", "after_at": 645},
        MCG_CLIP: {"account": "MCGlive", "archive": "mcg", "episode_id": "pwhvqgCGV0w",
                   "start": 754, "end": 800, "before": "b", "after": "a", "after_at": 802},
        "2116000000000000032": {"account": "MCGlive", "archive": "mcg",
                                "episode_id": "EOkDv8qm8ik", "start": 1, "end": 2},
        "2116000000000000033": {"account": "MCGlive", "archive": "mcg",
                                "episode_id": "notOnShelf1", "start": 1, "end": 2},
        "2116000000000000034": {"account": "MCGlive", "archive": "rasmr",
                                "episode_id": "2bev0UDp79Y", "start": 1, "end": 2}}))
    with gzip.open(tmp_path / "mcg_summaries.json.gz", "wt") as fh:
        json.dump({"2NK_MBlFvtM": {"tldr": "Bitcoin held 75.2K.", "model": "m", "pinned": True,
                                   "topics": [{"t": 249, "text": "the chart"},
                                              {"t": 1500, "text": "in the minutes X missed"},
                                              {"t": 3600, "text": "an hour in"},
                                              {"t": "60", "text": "a bad time"},
                                              {"t": 60}]},
                   "noclock0000": {"tldr": "", "topics": [], "pinned": True},
                   "Y9xI81lOP7U": {"tldr": "written before topics were pinned", "model": "m",
                                   "topics": [{"t": 60, "text": "maybe minutes late"}]}}, fh)
    (tmp_path / "tg.json").write_text(json.dumps({
        "EOkDv8qm8ik": {"broadcast": "https://x.com/i/broadcasts/1pJdRDqeOYnKW",
                        "offset": -3, "post": TG_POST}}))
    (tmp_path / "threadguy_index.json").write_text(json.dumps([
        {"id": "EOkDv8qm8ik", "title": "MARKET OPEN: Binance Suffers the BIGGEST HACK EVER",
         "published_at": "2026-10-30", "format": "stream"}]))
    with gzip.open(tmp_path / "threadguy_summaries.json.gz", "wt") as fh:
        json.dump({"EOkDv8qm8ik": {"tldr": "The Binance hack, live.", "model": "m", "pinned": True,
                                   "topics": [{"t": 1, "text": "before X began"},
                                              {"t": 600, "text": "ten minutes in"}]}}, fh)
    (tmp_path / "x_links.json").write_text(json.dumps({
        LINK_POST: {"broadcast": "https://x.com/i/broadcasts/1pJdRDqeOYnKW", "t": 6007},
        MB_LINK_POST: {"broadcast": "https://x.com/i/broadcasts/1dxYlaZbXWQJX", "t": 5993},
        "2116000000000000010": {"broadcast": "https://x.com/i/broadcasts/1UnknownShow1", "t": 60},
        "2116000000000000011": {"broadcast": "https://www.youtube.com/watch?v=EOkDv8qm8ik", "t": 60},
        "2116000000000000012": {"broadcast": "https://evil.example/i/broadcasts/1pJdRDqeOYnKW", "t": 9},
        "2116000000000000013": {"broadcast": "https://x.com/i/broadcasts/1pJdRDqeOYnKW", "t": "9"},
        MB_POST: {"broadcast": "https://x.com/i/broadcasts/1dxYlaZbXWQJX", "t": 5},
        YT_MB_POST: {"youtube": "Ep24onYTube", "t": 5805},
        YT_TG_POST: {"youtube": "EOkDv8qm8ik", "t": 600},
        YT_MCG_POST: {"youtube": "pwhvqgCGV0w", "t": 754},
        YT_YOUTUBE_EP: {"youtube": "KcazozbZHd0", "t": 30},
        "2116000000000000014": {"youtube": "O4wBUysNe2k", "t": 1129},    # Rogan: Elon's, left out
        "2116000000000000015": {"youtube": "Ep24onYTube", "t": 9000},    # past the last anchor
        "2116000000000000016": {"youtube": "../../etc/x", "t": 5},
        "2116000000000000017": {"youtube": "EOkDv8qm8ik", "t": -1}}))
    (tmp_path / "youtube_map.json").write_text(json.dumps({
        EP24: [[5900.0, "Ep24onYTube", 5800.0], [5910.0, "Ep24onYTube", 5810.0],
               [5920.0, "Ep24onYTube", 5821.0]]}))
    monkeypatch.setattr(youtube_map, "_PATH", tmp_path / "youtube_map.json")
    links = json.loads((tmp_path / "broadcast_links.json").read_text())
    monkeypatch.setattr(podcast, "_broadcast_players", lambda: links)
    for name, value in (("SUMMARIES", "summaries.json.gz"),
                        ("BROADCAST_LINKS", "broadcast_links.json"),
                        ("MCG_PAIRS", "mcg.json"), ("RASMR_PAIRS", "rasmr.json"),
                        ("RASMR_SHELF", "rasmr_index.json"),
                        ("MCG_SHELF", "mcg_index.json"), ("CLIPS", "x_clips.json"),
                        ("MCG_SUMMARIES", "mcg_summaries.json.gz"), ("LINKS", "x_links.json"),
                        ("THREADGUY_PAIRS", "tg.json"), ("THREADGUY_SHELF", "threadguy_index.json"),
                        ("THREADGUY_SUMMARIES", "threadguy_summaries.json.gz")):
        monkeypatch.setattr(x_posts, name, tmp_path / value)
    caches = (x_posts.shelf, x_posts._summaries, x_posts._clips, x_posts._mcg_summaries,
              x_posts._threadguy_summaries, x_posts._links, x_posts._broadcasts,
              youtube_map._anchors)
    for cached in caches:
        cached.cache_clear()
    yield tmp_path
    for cached in caches:
        cached.cache_clear()


def test_each_kind_of_post_lands_in_its_archive_on_its_clock(files):
    shelf = x_posts.shelf()
    assert shelf[MB_POST] == {"archive": "podcast", "episode_id": f"x-{MB_POST}",
                              "clock": {"offset": 0}}
    assert shelf["2115904746355851479"]["archive"] == "podcast"
    assert shelf[MCG_POST]["archive"] == "mcg"
    assert shelf[MCG_POST]["clock"]["sections"][0]["offset"] == -128
    assert shelf[MCG_POST]["title"] == "MCG Live: Binance"
    assert shelf[RASMR_PAIRED]["clock"] == {"offset": -15}


def test_a_stream_indexed_from_x_wins_over_its_youtube_pair(files):
    """Its transcript is on X's clock already: offset 0, not the pair's -40."""
    row = x_posts.shelf()[RASMR_X_ONLY]
    assert row["episode_id"] == f"x-{RASMR_X_ONLY}" and row["clock"] == {"offset": 0}


def test_ids_that_are_not_post_ids_never_reach_the_shelf(files):
    shelf = x_posts.shelf()
    assert all(re.fullmatch(r"[0-9]{5,25}", post) for post in shelf)
    assert "KcazozbZHd0" not in json.dumps(x_posts.listing())


def test_a_pair_with_no_measured_clock_gets_a_button_and_no_seeking(files):
    row = x_posts.describe("2099999999999999999")
    assert row["seekable"] is False and row["times"] == {}


def test_one_broken_file_costs_only_its_own_archive(files):
    (files / "mcg.json").write_text("{ not json")
    x_posts.shelf.cache_clear()
    listing = x_posts.listing()
    assert MCG_POST not in listing and {"podcast", "rasmr"} <= set(listing.values())
    assert listing[YT_MCG_POST] == "mcg", "a YouTube link needs MCG's shelf, not its pairs"


# --- the clocks ------------------------------------------------------------

@pytest.mark.parametrize("seconds,clock,expected", [
    (100, {"offset": 0}, 100),
    (100, {"offset": -15}, 85),
    (10, {"offset": -15}, None),          # before X's stream began
    (600, {"sections": [{"from": 0, "offset": -128, "to": 1418}]}, 472),
    (1500, {"sections": [{"from": 0, "offset": -128, "to": 1418},
                         {"from": 1961, "offset": -161, "to": None}]}, None),
    (5000, {"sections": [{"from": 1961, "offset": -161, "to": None}]}, 4839),
    (100, {}, None),
])
def test_a_transcript_second_on_x(seconds, clock, expected):
    assert x_posts.to_x(seconds, clock) == expected


def test_only_real_times_convert():
    times = x_posts.times_in("[1:03:00]–[1:20:00], 14:25, a 3:1 ratio, 9:75, 1:61:00, "
                             "12:30:45:10, 2026-10-30 10:15", {"offset": 0})
    assert times == {"1:03:00": 3780, "1:20:00": 4800, "14:25": 865, "10:15": 615}


@pytest.mark.parametrize("text", ["CPI prints at 8:30 ET", "the open at 9:30am",
                                  "around 1:20 pm", "at 10:00 UTC", "8:30 ET and later 8:30"])
def test_a_time_of_day_is_never_a_button(text):
    """It would play minute eight of the show for "8:30 ET"."""
    assert x_posts.times_in(text, {"offset": 0}) == {}


def test_words_that_only_start_like_a_zone_leave_the_time_alone():
    assert x_posts.times_in("at 1:39:33 eth dumped, 14:25 amazing, 2:00 etc", {"offset": 0}) == {
        "1:39:33": 5973, "14:25": 865, "2:00": 120}


def test_the_browser_finds_the_same_times_the_server_converts():
    """Two copies of one pattern, in two languages. If they drift, a time
    the server converted shows as a plain word, or the reverse."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    samples = ["at 1:39:33 and 14:25", "ratio 3:1 and 12:30:45:10", "[0:08:01]–[0:08:16]",
               "10:5 and 100:00 and 1:2:3", "2026-10-30 10:15:00Z", "x1:00 1:00x"]
    script = (f"const M=require({json.dumps(str(EXT / 'lib.js'))});const out={{}};"
              f"for(const s of {json.dumps(samples)}){{out[s]=M.timeTokens(s)}}"
              "console.log(JSON.stringify(out))")
    found = json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                      text=True, timeout=60, check=True).stdout)
    for sample in samples:
        assert found[sample] == x_posts.TIME.findall(sample), sample


def test_the_panel_names_its_post(files):
    row = x_posts.describe(MB_POST)
    assert row["title"] == "Ansem on X: gold" and row["show"] == "Ansem on X"
    assert row["times"] == {"0:01:40": 100, "0:04:57": 297}
    assert row["page"] == f"https://search.lexthedev.com/demo/podcast.html#ep-x-{MB_POST}"
    mcg = x_posts.describe(MCG_POST)
    assert mcg["page"].endswith("/demo/mcg.html")
    assert x_posts.describe("1234567") is None


def test_an_mcg_live_show_on_x_opens_with_its_summary_on_xs_clock(files):
    """Summary times are on YouTube's clock; the pair moves them to X's.
    0:04:09 is 249 - 128; 0:25:00 falls in the minutes X's stream missed,
    so it stays words; 1:00:00 is 3600 - 161."""
    row = x_posts.describe(MCG_POST)
    assert row["summary"] == ("**TL;DR** Bitcoin held 75.2K.\n\n**Topics**\n"
                              "- [0:04:09] the chart\n- [0:25:00] in the minutes X missed\n"
                              "- [1:00:00] an hour in")
    assert row["times"] == {"0:04:09": 121, "1:00:00": 3439}


def test_a_threadguy_show_on_x_has_his_summary_and_answers_on_xs_clock(files, client):
    """X runs three seconds behind YouTube on this one: 0:10:00 is 597.
    A time before X's stream began stays words."""
    row = x_posts.describe(TG_POST)
    assert row["archive"] == "threadguy" and row["archive_name"] == "ThreadGuy"
    assert row["title"].startswith("MARKET OPEN: Binance")
    assert row["times"] == {"0:10:00": 597}
    assert row["page"] == "https://search.lexthedev.com/demo/threadguy.html"
    r = client.post("/v1/x/ask", json={"post": TG_POST, "question": "what about binance"})
    assert r.status_code == 200 and r.json()["times"] == {"0:10:00": 597}
    assert client.app.state.threadguy.calls[-1]["episode_id"] == "EOkDv8qm8ik"


def test_an_mcg_show_with_no_summary_or_an_empty_one_has_none(files):
    assert x_posts._mcg_markdown("noclock0000") is None
    assert x_posts._mcg_markdown("not-on-file") is None


def test_a_summary_whose_times_were_never_pinned_is_not_shown(files):
    """Its times could be minutes late, and here a time moves the video."""
    assert x_posts._mcg_markdown("Y9xI81lOP7U") is None
    for pinned in (False, "yes", 1, None):
        store = {"e": {"tldr": "t", "topics": [], "pinned": pinned}}
        assert x_posts._notes_markdown(store, "e") is None, pinned


# --- clips -----------------------------------------------------------------

BROADCAST = "https://x.com/i/broadcasts/1dxYlaZbXWQJX"


def test_a_placed_clip_says_where_it_is_from_and_what_came_after(files):
    row = x_posts.describe(CLIP_POST)
    assert row["kind"] == "clip" and row["episode_id"] == EP24
    assert row["title"] == "Market Bubble Episode 24" and row["seekable"] is True
    assert row["clip"] == {
        "at": "1:39:53", "until": "1:41:12", "watch": f"{BROADCAST}?t=5993", "seeks": True,
        "before": "lab diamonds.", "after": "How does MicroStrategy look?",
        "after_at": "1:41:18", "after_watch": f"{BROADCAST}?t=6078"}


def test_a_time_inside_the_clip_plays_in_it_and_every_time_opens_the_show(files):
    row = x_posts.describe(CLIP_POST)
    assert row["times"] == {"1:40:05": 12}, "the clip's own second, from its start"
    assert row["links"] == {"0:04:31": f"{BROADCAST}?t=271",
                            "1:40:05": f"{BROADCAST}?t=6005",
                            "2:00:00": f"{BROADCAST}?t=7200"}
    assert "8:30" not in row["links"], "a time of day opens nothing"


def test_the_last_seconds_of_a_clip_still_play_in_it(files):
    clip = x_posts.shelf()[CLIP_POST]
    times, _ = x_posts.marks("1:39:52 1:41:12 1:41:17 1:41:18", clip)
    assert times == {"1:41:12": 79, "1:41:17": 84}


def test_a_recording_answers_with_no_links(files):
    times, links = x_posts.marks("at 0:01:40", x_posts.shelf()[MB_POST])
    assert times == {"0:01:40": 100} and links == {}
    assert x_posts.describe(MB_POST)["kind"] == "recording"


def test_a_whole_recording_never_turns_into_a_clip(files):
    """A clip row for a post that is the whole show loses: that post gets
    the whole show's panel, and its times play the whole show."""
    assert x_posts.shelf()[MB_POST] == {"archive": "podcast", "episode_id": f"x-{MB_POST}",
                                        "clock": {"offset": 0}}


def test_a_clip_row_wrong_in_any_way_is_left_out_whole(files):
    shelf = x_posts.shelf()
    assert CLIP_POST in shelf and "2116000000000000001" in shelf
    for n in range(2, 10):
        assert f"211600000000000000{n}" not in shelf, n
    assert "not-a-post" not in shelf


def test_a_clip_of_a_youtube_copy_opens_youtube_at_the_second(files):
    row = x_posts.describe("2116000000000000001")
    assert row["clip"]["watch"] == "https://www.youtube.com/watch?v=KcazozbZHd0&t=60s"
    assert row["clip"]["after_at"] is None and row["clip"]["after_watch"] is None


def test_a_threadguy_clip_opens_his_show_on_x_through_the_pair(files):
    """Indexed from YouTube, so the clip's seconds are YouTube's; the show
    aired on X too, three seconds behind, and opens there."""
    row = x_posts.describe(TG_CLIP)
    assert (row["kind"], row["archive_name"]) == ("clip", "ThreadGuy")
    assert row["title"].startswith("MARKET OPEN") and row["published_at"] == "2026-10-30"
    on_x = "https://x.com/i/broadcasts/1pJdRDqeOYnKW"
    assert row["clip"]["watch"] == f"{on_x}?t=587" and row["clip"]["seeks"] is True
    assert row["clip"]["after_watch"] == f"{on_x}?t=642"
    assert row["times"] == {"0:10:00": 10}, "inside the clip, its own second"
    assert row["links"] == {"0:10:00": f"{on_x}?t=597",
                            "0:00:01": "https://www.youtube.com/watch?v=EOkDv8qm8ik&t=1s"}, \
        "said before X began: YouTube, at the same words"
    assert x_posts.listing()[TG_CLIP] == "threadguy"


def test_an_mcg_clip_of_a_show_never_on_x_opens_youtube(files):
    row = x_posts.describe(MCG_CLIP)
    assert (row["archive_name"], row["title"]) == ("MCG Live", "This Trader Caught $ORBIO")
    assert row["clip"]["watch"] == "https://www.youtube.com/watch?v=pwhvqgCGV0w&t=754s"
    assert row["clip"]["after_watch"] == "https://www.youtube.com/watch?v=pwhvqgCGV0w&t=802s"


def test_a_clip_is_only_shown_from_a_show_its_own_archive_holds(files):
    shelf = x_posts.shelf()
    assert "2116000000000000032" not in shelf, "ThreadGuy's show, filed as MCG's"
    assert "2116000000000000033" not in shelf, "a show the archive doesn't hold"
    assert "2116000000000000034" not in shelf, "rasmr's streams aren't placed as clips"


def test_a_clip_of_an_uploaded_video_promises_no_second_it_cannot_open(files):
    """X opens a post's own video at 0:00 whatever ?t= says. The full
    video is still offered, but no time links to it."""
    row = x_posts.describe("2115461279163052437")
    upload = "https://x.com/blknoiz06/status/2115418459765113280"
    assert row["clip"]["watch"] == f"{upload}?t=1719"
    assert row["clip"]["seeks"] is False and row["clip"]["after_watch"] is None
    assert row["links"] == {}
    assert x_posts.describe(CLIP_POST)["clip"]["seeks"] is True


def test_an_address_that_is_not_a_web_link_is_never_offered(files):
    assert x_posts.watch("x-2111111111111111111", 60) is None
    assert x_posts.watch("x-not-in-the-archive", 60) is None


@pytest.mark.parametrize("url,ok", [
    (f"{BROADCAST}?t=5993", True),
    ("https://x.com/MarketBubble/status/2115904746355851479?t=60", True),
    ("https://www.youtube.com/watch?v=KcazozbZHd0&t=60s", True),
    (f"{BROADCAST}?t=5993&next=https://evil.example", False),
    ("https://x.com.evil.example/i/broadcasts/1dxYlaZbXWQJX?t=1", False),
    ("http://x.com/i/broadcasts/1dxYlaZbXWQJX?t=1", False),
    ("https://x.com/i/broadcasts/../../login?t=1", False),
    ("javascript:alert(1)//https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=1", False),
    ("https://youtube.com.evil/watch?v=KcazozbZHd0&t=60s", False),
    ("https://www.youtube.com/watch?v=KcazozbZHd0&t=60s\n", False),
    (f"{BROADCAST}?t=1234567", False),
])
def test_a_link_to_the_show_has_one_of_three_shapes(url, ok):
    assert bool(x_posts.LINK.fullmatch(url)) is ok


def test_the_browser_allows_exactly_the_links_the_server_does():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    samples = [f"{BROADCAST}?t=5993", f"{BROADCAST}?t=5993#x", "https://x.com/a/status/12345?t=1",
               "https://x.com/abcdefghijklmnop/status/12345?t=1",
               "https://www.youtube.com/watch?v=KcazozbZHd0&t=60s",
               "https://www.youtube.com/watch?v=KcazozbZHd0&t=60", "https://evil.example/",
               "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=60\n"]
    script = (f"const M=require({json.dumps(str(EXT / 'lib.js'))});"
              f"console.log(JSON.stringify({json.dumps(samples)}.map(s=>M.LINK.test(s))))")
    found = json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                      text=True, timeout=60, check=True).stdout)
    assert found == [bool(x_posts.LINK.fullmatch(s)) for s in samples]
    script = (f"const M=require({json.dumps(str(EXT / 'lib.js'))});"
              f"console.log(JSON.stringify({json.dumps(samples)}.map(s=>M.seeks(s))))")
    found = json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                      text=True, timeout=60, check=True).stdout)
    assert found == [bool(x_posts.SEEKS.fullmatch(s)) for s in samples]


# --- the routes ------------------------------------------------------------

class _Stub:
    def __init__(self, *a, **k):
        pass


class _Archive:
    """A PodcastIndex stand-in that records how it was asked."""

    def __init__(self, answer="Around 0:01:40 he called the top. At 9:99 nothing."):
        self.answer, self.calls = answer, []

    async def search(self, query, top_k=None, instruction=None, model=None,
                     episode_id=None):
        self.calls.append({"query": query, "episode_id": episode_id,
                           "instruction": instruction})
        hit = PodcastHit(episode_id=episode_id, title="t", start_seconds=100.0,
                         timestamp="1:40", deep_link="https://x.com", text="words",
                         score=1.0)
        return PodcastSearchResponse(answer=self.answer, hits=[hit], model="m")


@pytest.fixture
def client(files, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    get_settings.cache_clear()
    for name in ("Retriever", "ConciergeAgent", "IngestionPipeline", "PodcastIndex"):
        monkeypatch.setattr(main_module, name, _Stub)
    with TestClient(main_module.app) as c:
        c.app.state.podcast = _Archive()
        c.app.state.mcg = _Archive("At 30:00 and 1:00:00 they said it.")
        c.app.state.rasmr = None
        c.app.state.threadguy = _Archive("At 0:10:00 he said it.")
        c.app.state.answers._store.clear() if hasattr(c.app.state.answers, "_store") else None
        yield c
    get_settings.cache_clear()


def test_the_shelf_is_public_cacheable_and_only_ids(client):
    r = client.get("/v1/x/shelf")
    assert r.status_code == 200
    assert "max-age=3600" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"
    body = r.json()
    assert body["posts"][MB_POST] == "podcast" and body["count"] == len(body["posts"])
    assert set(body["posts"].values()) <= {"podcast", "mcg", "rasmr", "threadguy"}


def test_a_post_opens_with_its_summary_and_x_seconds(client):
    body = client.get(f"/v1/x/post/{MB_POST}").json()
    assert body["summary"].startswith("**Topics**")
    assert body["times"]["0:04:57"] == 297


@pytest.mark.parametrize("post", ["1234567", "abc", "1" * 30, "..%2F..%2Fetc",
                                  "123456789012345678901234567890", "%00"])
def test_anything_but_a_known_post_is_not_found(client, post):
    assert client.get(f"/v1/x/post/{post}").status_code == 404


def test_a_question_is_answered_from_that_recording_only(client):
    r = client.post("/v1/x/ask", json={"post": MCG_POST, "question": "  what about binance  "})
    assert r.status_code == 200
    call = client.app.state.mcg.calls[-1]
    assert call["episode_id"] == "2NK_MBlFvtM", "the shelf picks the episode"
    assert call["query"] == "what about binance"
    assert "this one recording on X" in call["instruction"]
    # 30:00 is in the minutes the clocks moved; 1:00:00 is 3600 - 161.
    assert r.json()["times"] == {"1:00:00": 3439}


def test_the_caller_cannot_choose_an_episode_or_an_archive(client):
    r = client.post("/v1/x/ask", json={"post": MB_POST, "question": "q",
                                       "episode_id": "SOMETHING_ELSE",
                                       "archive": "elon", "namespace": "elon"})
    assert r.status_code == 200
    assert client.app.state.podcast.calls[-1]["episode_id"] == f"x-{MB_POST}"


@pytest.mark.parametrize("body", [
    {"post": MB_POST, "question": ""},
    {"post": MB_POST, "question": "   \n "},
    {"post": MB_POST, "question": "q" * 501},
    {"post": "abc", "question": "q"},
    {"post": MB_POST + "'; drop", "question": "q"},
    {"post": 2116220650058846455, "question": "q"},
    {"question": "q"},
    {"post": MB_POST},
])
def test_a_malformed_question_is_refused_before_anything_is_spent(client, body):
    assert client.post("/v1/x/ask", json=body).status_code == 422
    assert client.app.state.podcast.calls == []


def _spent():
    """(service-wide, this client) daily budget used so far."""
    from app.security import daily_budget, per_client_daily
    return daily_budget.state()["used"], sum(per_client_daily._counts.values())


@pytest.fixture
def budgets():
    from app.security import daily_budget, per_client_daily
    daily_budget._day, daily_budget._count = None, 0
    per_client_daily._counts.clear()
    yield
    per_client_daily._counts.clear()


def test_an_unknown_post_costs_nobody_anything(client, budgets):
    """Found in review: asks about posts that don't exist spent the
    service-wide budget, which has no refund, with no model call. 200 of
    them from one address took 200 off everybody's day."""
    # Five: inside the per-minute burst, which counts every request and
    # is not what this is about.
    for i in range(5):
        r = client.post("/v1/x/ask", json={"post": f"12345{i:02d}", "question": "q"})
        assert r.status_code == 404
    assert _spent() == (0, 0)


def test_an_archive_that_is_not_loaded_costs_nothing(client, budgets):
    r = client.post("/v1/x/ask", json={"post": RASMR_PAIRED, "question": "q"})
    assert r.status_code == 503
    assert _spent() == (0, 0)


def test_a_malformed_question_spends_no_budget(client, budgets):
    for body in ({"post": "abc", "question": "q"}, {"post": MB_POST, "question": ""}):
        assert client.post("/v1/x/ask", json=body).status_code == 422
    assert _spent() == (0, 0)


def test_one_answer_is_charged_once_and_its_repeat_is_free(client, budgets):
    for _ in range(3):
        assert client.post("/v1/x/ask", json={"post": MB_POST, "question": "the top"}
                           ).status_code == 200
    assert len(client.app.state.podcast.calls) == 1
    assert _spent() == (1, 1)


def test_an_exhausted_day_refuses_before_the_model_is_asked(client, budgets, monkeypatch):
    from app.security import daily_budget
    monkeypatch.setattr(daily_budget, "_limit", 1)
    assert client.post("/v1/x/ask", json={"post": MB_POST, "question": "one"}).status_code == 200
    assert client.post("/v1/x/ask", json={"post": MB_POST, "question": "two"}).status_code == 503
    assert len(client.app.state.podcast.calls) == 1


def test_a_market_bubble_answer_gets_the_pages_attribution_guard(client, monkeypatch):
    seen = []

    def correct(answer, hits):
        seen.append(answer)
        return answer.replace("he called", "one of the hosts called"), ["demoted"]
    monkeypatch.setattr(main_module.attribution, "correct", correct)
    r = client.post("/v1/x/ask", json={"post": MB_POST, "question": "who called the top"})
    assert seen and "one of the hosts called" in r.json()["answer"]


def test_asking_carries_every_limit_a_search_has():
    """The per-minute limits as dependencies; the two daily budgets are
    charged inside, after the checks that cost nothing (tested above)."""
    route = next(r for r in main_module.app.routes if getattr(r, "path", "") == "/v1/x/ask")
    deps = {d.call for d in route.dependant.dependencies}
    from app.security import global_rate_limit, public_rate_limit
    assert {public_rate_limit, global_rate_limit} <= deps
    source = (ROOT / "app" / "main.py").read_text()
    body = source[source.index("async def x_ask("):]
    assert body.index("await daily_budget(request)") < body.index("await index.search(")
    assert body.index("await per_client_daily(request)") < body.index("await index.search(")
    for path in ("/v1/x/shelf", "/v1/x/post/{post_id}"):
        route = next(r for r in main_module.app.routes if getattr(r, "path", "") == path)
        assert public_rate_limit in {d.call for d in route.dependant.dependencies}


# --- one episode, every way into the pool -----------------------------------

def test_one_episode_means_every_search_and_every_hit_is_that_episode(monkeypatch):
    import app.podcast as podcast
    from app.podcast import PodcastIndex
    filters, fetched, asked_now = [], [], []

    async def fake_embed(*a, **k):
        return [0.0, 1.0]

    async def about_now(self, q):
        asked_now.append(q)
        return None
    monkeypatch.setattr(podcast, "embed_query", fake_embed)
    monkeypatch.setattr(PodcastIndex, "_about_now", about_now)

    class Match:
        def __init__(self, episode, start):
            self.score = 0.9
            self.metadata = {"episode_id": episode, "start_seconds": start,
                             "title": "t", "url": "https://x.com/a/status/1",
                             "platform": "other", "text": "w"}

    class Index:
        def query(self, **kwargs):
            filters.append(kwargs.get("filter"))

            class R:
                # A filter that misbehaved: another episode comes back too.
                matches = [Match("x-1", 10.0), Match("x-OTHER", 20.0), Match("x-1", 30.0)]
            return R()

        def fetch(self, **kwargs):
            fetched.append(kwargs)
            return None

    class S:
        rerank_candidates = 50
        rerank_model = None
        voyage_model = "v"
        embedding_dimension = 2
        pinecone_read_timeout_seconds = 5
        retrieval_min_score = 0.0
        rerank_narrow_pool = 0
    index = PodcastIndex.__new__(PodcastIndex)
    index._settings, index._voyage, index._namespace = S(), None, "podcast"
    index._index = Index()
    looked_up = []

    class Terms:
        def lookup(self, query):
            looked_up.append(query)
            return ["a" * 32]

        def terms_in(self, query):
            return ["banks"]
    index._terms = Terms()
    hits = asyncio.run(index._retrieve("what did banks say", 5, only_episode="x-1"))
    assert hits and {h.episode_id for h in hits} == {"x-1"}
    assert filters and all("x-1" in json.dumps(f) for f in filters)
    assert not fetched and not looked_up, "no archive-wide exact-word reads for one episode"
    assert not asked_now, "recency does not apply inside one recording"


# --- the extension itself ----------------------------------------------------

def _manifest() -> dict:
    return json.loads((EXT / "manifest.json").read_text())


def test_it_asks_for_no_permission_it_does_not_use():
    m = _manifest()
    assert m["manifest_version"] == 3
    assert "permissions" not in m and "optional_permissions" not in m
    # Not even our own origin: the API answers any origin, so the
    # extension needs no host access to reach it, and none is one less
    # warning at install.
    assert "host_permissions" not in m
    assert [c["matches"] for c in m["content_scripts"]] == [["https://x.com/*"]]
    for key in ("externally_connectable", "web_accessible_resources",
                "optional_host_permissions", "oauth2", "key", "update_url"):
        assert key not in m, key
    assert m["content_security_policy"]["extension_pages"] == (
        "script-src 'self'; object-src 'none'; base-uri 'none'")


def test_every_file_the_manifest_names_is_there():
    m = _manifest()
    named = [m["background"]["service_worker"], *m["content_scripts"][0]["js"],
             *m["icons"].values()]
    for name in named:
        assert (EXT / name).is_file(), name


_SCRIPTS = ["lib.js", "background.js", "content.js"]


@pytest.mark.parametrize("pattern", [
    r"\.innerHTML\b", r"\.outerHTML\b", r"insertAdjacentHTML", r"document\.write",
    r"\beval\s*\(", r"new\s+Function\b", r"setTimeout\(\s*['\"]", r"setInterval\(\s*['\"]",
    r"DOMParser", r"createContextualFragment", r"\.srcdoc\b", r"http://(?!www\.w3\.org/2000/svg\b)",
    r"localStorage", r"document\.cookie", r"chrome\.storage", r"externally",
])
def test_no_script_parses_markup_runs_strings_or_keeps_data(pattern):
    for name in _SCRIPTS:
        code = (EXT / name).read_text()
        assert not re.search(pattern, code), f"{name}: {pattern}"


def test_only_the_background_talks_to_the_network_and_only_to_our_origin():
    assert "fetch(" not in (EXT / "content.js").read_text()
    assert "XMLHttpRequest" not in "".join((EXT / n).read_text() for n in _SCRIPTS)
    background = (EXT / "background.js").read_text()
    assert background.count("fetch(") == 1
    assert "fetch(MBS.API + path" in background
    # The only addresses written plainly anywhere in the scripts: ours, and
    # x.com in the check on who is sending a message. (X's patterns in
    # lib.js are escaped regex, not addresses.)
    assert set(re.findall(r"https?://[a-z0-9.-]+", "".join(
        (EXT / n).read_text() for n in _SCRIPTS).replace("http://www.w3.org/2000/svg", ""))) == {
        "https://search.lexthedev.com", "https://x.com"}


def test_the_background_answers_only_its_own_script_on_x():
    background = (EXT / "background.js").read_text()
    assert "sender.id !== chrome.runtime.id" in background
    assert 'return origin === "https://x.com";' in background
    assert 'credentials: "omit"' in background and 'redirect: "error"' in background


def test_the_extensions_own_tests_pass():
    """CI runs pytest, so the JavaScript runs here too: the logic and the
    background worker always, and the browser tests wherever a Chromium
    and ffmpeg are installed (they skip themselves elsewhere)."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    files = sorted((EXT / "test").glob("*.test.js")) + sorted((EXT / "test").glob("*.test.mjs"))
    run = subprocess.run(["node", "--test", *map(str, files)],
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]


# --- the privacy policy the store links to ----------------------------------

PRIVACY = ROOT / "demo" / "extension-privacy.html"


def test_the_privacy_policy_is_served_where_the_listing_points(client):
    r = client.get("/extension/privacy", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/demo/extension-privacy.html"
    page = client.get("/demo/extension-privacy.html")
    assert page.status_code == 200 and "Limited Use" in page.text


def test_what_the_privacy_policy_promises_is_what_the_code_does():
    """The page is a promise about this code. Each line here is one a
    change could break without anyone rereading the page."""
    page = re.sub(r"\s+", " ", PRIVACY.read_text())
    manifest = _manifest()
    # "asks for no permissions", "talks only to search.lexthedev.com"
    assert "None. It runs on <code>https://x.com/*</code>" in page
    for key in ("permissions", "host_permissions", "optional_permissions",
                "optional_host_permissions", "externally_connectable"):
        assert key not in manifest, key
    assert [c["matches"] for c in manifest["content_scripts"]] == [["https://x.com/*"]]
    # "logs ... its first 120 characters"
    assert "its first 120 characters" in page
    assert "q=body.question[:120]" in (ROOT / "app" / "main.py").read_text()
    # "the same question within 24 hours is answered from a cache"
    from app.answer_cache import AnswerCache
    assert AnswerCache()._ttl == 86_400.0 and "within 24 hours" in page
    # "at most once an hour"
    assert "SHELF_TTL_MS = 60 * 60 * 1000" in (EXT / "background.js").read_text()
    assert "at most once an hour" in page
    # Never an em or en dash: the page speaks for the project.
    assert not re.search("[–—]", page)


# --- what the 1 Nov review found ------------------------------------------

def test_a_client_at_its_cap_spends_nothing_of_the_day(client, budgets, monkeypatch):
    """Per-client first: a client over its cap is turned away before the
    day is charged. In the other order one address emptied the day."""
    from app.security import daily_budget, per_client_daily, public_rate_limit
    monkeypatch.setattr(public_rate_limit, "_rpm", 100_000)
    monkeypatch.setattr(public_rate_limit, "_burst", 100_000)
    monkeypatch.setattr(per_client_daily, "_limit", 2)
    monkeypatch.setattr(daily_budget, "_limit", 10)
    codes = [client.post("/v1/x/ask", json={"post": MB_POST, "question": f"question {i}"}).status_code
             for i in range(6)]
    assert codes == [200, 200, 429, 429, 429, 429]
    assert daily_budget.state()["used"] == 2


def test_a_day_that_is_out_gives_the_client_its_slot_back(client, budgets, monkeypatch):
    from app.security import daily_budget, per_client_daily
    monkeypatch.setattr(daily_budget, "_limit", 1)
    assert client.post("/v1/x/ask", json={"post": MB_POST, "question": "one"}).status_code == 200
    assert client.post("/v1/x/ask", json={"post": MB_POST, "question": "two"}).status_code == 503
    assert sum(per_client_daily._counts.values()) == 1


def test_a_cached_answer_on_the_site_costs_nobody_a_slot(client, budgets, monkeypatch):
    """The front door charged the day for answers it already had; only the
    client's slot came back, so the day drained with no model call."""
    import app.main as m
    from app.answer_cache import make_key
    from app.security import daily_budget, per_client_daily, public_rate_limit
    monkeypatch.setattr(public_rate_limit, "_rpm", 100_000)
    monkeypatch.setattr(public_rate_limit, "_burst", 100_000)
    monkeypatch.setattr(daily_budget, "_limit", 10)
    client.app.state.answers.put(make_key(m._timed("what about gold"), surface="front-door",
                                          top_k=None), {"answer": "cached"})
    codes = {client.post("/v1/search", json={"query": "what about gold"}).status_code
             for _ in range(12)}
    assert codes == {200}
    assert daily_budget.state()["used"] == 0 and sum(per_client_daily._counts.values()) == 0


@pytest.mark.parametrize("path,size,code", [
    ("/v1/x/ask", 15_000, 422),        # under the extension's cap: read and refused as a body
    ("/v1/x/ask", 17_000, 413),
    ("/v1/search", 200_000, 422),
    ("/v1/search", 300_000, 413),
])
def test_a_body_over_its_routes_limit_is_refused_before_it_is_read(client, path, size, code):
    body = b'{"pad": "' + b"0" * size + b'"}'
    assert client.post(path, content=body, headers={"content-type": "application/json"}).status_code == code


def test_a_body_with_no_length_is_held_to_the_same_limit(client):
    def chunks(total):
        yield b'{"post": "' + MB_POST.encode() + b'", "question": "q", "pad": "'
        for _ in range(total // 4096):
            yield b"0" * 4096
        yield b'"}'
    big = client.post("/v1/x/ask", content=chunks(40_000), headers={"content-type": "application/json"})
    assert big.status_code == 413
    small = client.post("/v1/x/ask", content=chunks(4_000), headers={"content-type": "application/json"})
    assert small.status_code == 200, "a small chunked body still reaches the route whole"


def test_the_admin_ingest_keeps_room_for_whole_episodes():
    import app.main as m
    assert m._body_limit("/v1/podcast/ingest") == 20_000_000
    assert m._body_limit("/v1/x/ask") == 16_000 and m._body_limit("/v1/xyz") == 256_000


def test_half_an_emoji_in_a_question_is_a_422_not_a_500(client, budgets):
    body = '{"post": "' + MB_POST + '", "question": "abc \\ud83d"}'
    r = client.post("/v1/x/ask", content=body, headers={"content-type": "application/json"})
    assert r.status_code in (200, 422)
    r = client.post("/v1/x/ask", content='{"post": "\\ud83d", "question": "q"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert "input" not in r.json()["detail"][0], "the bad value is not echoed back"


def test_the_privacy_policys_log_line_carries_what_it_says(client, budgets, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        client.post("/v1/x/ask", json={"post": MB_POST, "question": "what about gold"})
    line = next(r.getMessage() for r in caplog.records if "x_extension_asks" in r.getMessage())
    logged = json.loads(line.split("ANALYTICS ", 1)[1])
    assert logged == {"event": "x_extension_asks", "post": MB_POST, "q": "what about gold"}


def test_the_daily_caps_log_line_names_no_address(caplog):
    import logging

    from app.security import PerClientDailyBudget
    budget = PerClientDailyBudget(limit=1)
    request = type("R", (), {"client": type("C", (), {"host": "203.0.113.9"})(), "headers": {}})()
    asyncio.run(budget(request))
    from fastapi import HTTPException
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException):
        asyncio.run(budget(request))
    assert caplog.records and not any("203.0.113.9" in r.getMessage() for r in caplog.records)


def test_the_extension_knows_every_archive_the_server_names():
    """An archive the server sends that the extension's list lacks makes
    every one of its posts fail in the browser, and fixing that means a
    new version through the store's review."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = (f"const M=require({json.dumps(str(EXT / 'lib.js'))});"
              "console.log(JSON.stringify(M.ARCHIVES))")
    found = json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                      text=True, timeout=60, check=True).stdout)
    assert sorted(found) == sorted(x_posts.ARCHIVE_NAMES) == sorted(x_posts._PAGES)


@pytest.mark.parametrize("path", ["/google925ad965c5e0aab5.html",
                                  "/home/google925ad965c5e0aab5.html"])
def test_search_console_can_see_the_sites_verification_file(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.text == "google-site-verification: google925ad965c5e0aab5.html"



# --- our link posts ----------------------------------------------------------

@pytest.mark.parametrize("seconds,clock,expected", [
    (6007, {"offset": -3}, 6010),
    (2, {"offset": 3}, None),                      # before the show began on X
    (121, {"sections": [{"from": 0, "offset": -128, "to": 1418},
                        {"from": 1961, "offset": -161, "to": None}]}, 249),
    (1400, {"sections": [{"from": 0, "offset": -128, "to": 1418},
                         {"from": 1961, "offset": -161, "to": None}]}, None),   # the gap
    (3439, {"sections": [{"from": 0, "offset": -128, "to": 1418},
                         {"from": 1961, "offset": -161, "to": None}]}, 3600),
    (60, {}, None),
])
def test_a_second_on_x_back_to_the_transcript(seconds, clock, expected):
    assert x_posts.from_x(seconds, clock) == expected
    if expected is not None:
        assert x_posts.to_x(expected, clock) == seconds, "the inverse of to_x"


def test_a_link_post_points_at_its_show_and_second(files):
    row = x_posts.describe(LINK_POST)
    assert row["kind"] == "link" and row["archive"] == "threadguy"
    assert row["episode_id"] == "EOkDv8qm8ik" and row["title"].startswith("MARKET OPEN")
    assert row["link"] == {"at": "1:40:10",
                           "watch": "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=6007"}
    assert row["times"] == {}, "the card cannot start at a second; times open the broadcast"
    assert row["links"] == {"0:10:00": "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=597",
                            "0:00:01": "https://www.youtube.com/watch?v=EOkDv8qm8ik&t=1s"}, \
        "said before X's stream began: it still opens, on the YouTube copy"
    assert row["seekable"] is True


def test_a_market_bubble_link_post_has_its_summary(files):
    row = x_posts.describe(MB_LINK_POST)
    assert row["episode_id"] == EP24 and row["link"]["at"] == "1:39:53"
    assert row["summary"].startswith("**Topics**")
    assert row["links"]["0:04:31"] == "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=271"


def test_an_old_youtube_link_to_market_bubble_opens_on_x_at_the_same_words(files):
    """Before October we linked the upload. The upload is cut from the
    broadcast, so the second moves back onto X's clock through the map:
    5805 on YouTube is 5905 on X here."""
    row = x_posts.describe(YT_MB_POST)
    assert row["kind"] == "link" and row["archive"] == "podcast" and row["episode_id"] == EP24
    assert row["link"] == {"at": "1:38:25",
                           "watch": "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=5905"}
    assert row["summary"].startswith("**Topics**")
    assert row["links"]["0:04:31"] == "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=271"


def test_a_youtube_link_to_a_show_paired_with_x_opens_on_x(files):
    row = x_posts.describe(YT_TG_POST)
    assert row["archive"] == "threadguy" and row["episode_id"] == "EOkDv8qm8ik"
    assert row["link"] == {"at": "0:10:00",
                           "watch": "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=597"}
    assert row["summary"].startswith("**TL;DR** The Binance hack")


def test_a_youtube_link_to_a_show_that_never_aired_on_x_stays_on_youtube(files):
    row = x_posts.describe(YT_MCG_POST)
    assert row["archive"] == "mcg" and row["title"] == "This Trader Caught $ORBIO"
    assert row["link"] == {"at": "0:12:34",
                           "watch": "https://www.youtube.com/watch?v=pwhvqgCGV0w&t=754s"}
    assert row["summary"] is None and row["seekable"] is True
    _, links = x_posts.marks("around 0:05:00 he said", x_posts.shelf()[YT_MCG_POST])
    assert links == {"0:05:00": "https://www.youtube.com/watch?v=pwhvqgCGV0w&t=300s"}


def test_a_market_bubble_episode_held_only_from_youtube_plays_there(files):
    row = x_posts.describe(YT_YOUTUBE_EP)
    assert row["archive"] == "podcast" and row["episode_id"] == "KcazozbZHd0"
    assert row["link"]["watch"] == "https://www.youtube.com/watch?v=KcazozbZHd0&t=30s"


def test_a_link_to_what_we_dont_hold_or_cant_place_gets_no_button(files):
    shelf = x_posts.shelf()
    for post in ("2116000000000000010", "2116000000000000011",
                 "2116000000000000012", "2116000000000000013",
                 "2116000000000000014", "2116000000000000015",
                 "2116000000000000016", "2116000000000000017"):
        assert post not in shelf, post
    assert shelf[MB_POST].get("kind", "recording") == "recording", \
        "a post that is the whole recording keeps that panel"


def test_a_question_about_a_link_post_is_asked_of_its_show(client):
    r = client.post("/v1/x/ask", json={"post": LINK_POST, "question": "what about mando"})
    assert r.status_code == 200
    assert client.app.state.threadguy.calls[-1]["episode_id"] == "EOkDv8qm8ik"
    assert r.json()["links"] == {"0:10:00": "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=597"}

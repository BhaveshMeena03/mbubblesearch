"""A video Ansem posted on X plays in the page, from the second asked for.

"ansem elsewhere isnt jump to exact second" (22 November 2026). X has no
link that opens a posted video at a time, so every time in the summary of
one of his own videos was plain text, and a passage from one under an
answer offered "play on X", which opened it at 0:00. The page now plays
the video's file. X's video server answers for it only when the request
names no site, which he was told and chose; see app/x_videos.py.
"""

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import main, x_videos
from app.schemas import his_own
from app.summaries import load_shipped

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "demo" / "podcast.html").read_text()
NODE = shutil.which("node")

MP4 = "https://video.twimg.com/amplify_video/2116220293899280384/vid/avc1/{}/abcDEF_gh-1.mp4?tag=29"
POSTER = "https://pbs.twimg.com/amplify_video_thumb/2116220293899280384/img/DYNVV6ERpSJF7qPK.jpg?name=orig"


def found(*heights, extra=()):
    """What yt-dlp hands back for a post, cut down to what is read."""
    files = [{"url": MP4.format(f"{h * 16 // 9}x{h}"), "height": h, "protocol": "https"}
             for h in heights]
    return {"formats": list(extra) + files, "thumbnail": POSTER, "duration": 967.4}


# --- which file ---------------------------------------------------------------

def test_it_keeps_the_720_for_a_laptop_and_the_360_for_a_phone():
    kept = x_videos.pick(found(270, 360, 720, 1080))
    assert kept == {"mp4": MP4.format("1280x720"), "small": MP4.format("640x360"),
                    "poster": POSTER}


def test_a_playlist_is_never_kept_because_chrome_cannot_play_one():
    playlist = {"url": "https://video.twimg.com/amplify_video/1/pl/avc1/1280x720/x.m3u8",
                "height": 720, "protocol": "m3u8_native"}
    assert x_videos.pick(found(360, extra=[playlist]))["mp4"] == MP4.format("640x360")
    assert x_videos.pick({"formats": [playlist]}) is None


def test_when_every_file_is_too_tall_it_takes_the_shortest():
    assert x_videos.pick(found(1080, 2160)) == {
        "mp4": MP4.format("1920x1080"), "small": MP4.format("1920x1080"), "poster": POSTER}


@pytest.mark.parametrize("info", [None, {}, {"formats": None}, {"formats": ["x", 3, {}]}, "post",
                                  {"formats": [{"url": "https://evil.example/a.mp4", "height": 720}]},
                                  {"formats": [{"url": MP4.format("1280x720"), "height": "720"}]}])
def test_nothing_usable_is_nothing_and_not_an_error(info):
    assert x_videos.pick(info) is None


def test_a_video_shot_upright_is_measured_by_its_shorter_side():
    """720x1280 is a 720. Measured by height alone it was a 1280, and a
    laptop was handed the 320 pixel file."""
    tall = [{"url": MP4.format(f"{w}x{h}"), "width": w, "height": h}
            for w, h in ((320, 568), (480, 852), (720, 1280))]
    kept = x_videos.pick({"formats": tall})
    assert kept == {"mp4": MP4.format("720x1280"), "small": MP4.format("320x568")}


def test_a_post_with_one_video_in_a_list_is_that_video_and_two_is_none():
    """yt-dlp hands a post with several videos back as a list. The
    transcript is of one of them and nothing here says which."""
    one = found(360, 720)
    assert x_videos.pick({"_type": "playlist", "entries": [one]}) == x_videos.pick(one)
    assert x_videos.pick({"_type": "playlist", "entries": [one, found(720)]}) is None
    for empty in ([], None.__class__, 5, [None, "x"]):
        assert x_videos.pick({"entries": empty}) is None


def test_a_poster_from_anywhere_else_is_dropped():
    info = found(720)
    info["thumbnail"] = "https://evil.example/poster.jpg"
    assert "poster" not in x_videos.pick(info)


# --- what is handed to a page ---------------------------------------------------

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "http://video.twimg.com/a.mp4", "https://video.twimg.com.evil.example/a.mp4",
    "https://evil.example/https://video.twimg.com/a.mp4", "https://video.twimg.com/a.mp4?tag=1&x=<script>",
    "https://video.twimg.com/a.mp4#\"><img src=x>", "https://video.twimg.com/a.m3u8", "", None, 7])
def test_only_xs_own_video_host_is_ever_handed_out(monkeypatch, bad):
    monkeypatch.setattr(x_videos, "_rows", lambda: {"x-1": {"mp4": bad, "small": bad}})
    assert x_videos.for_episode("x-1") is None
    monkeypatch.setattr(x_videos, "_rows", lambda: {"x-1": {
        "mp4": MP4.format("1280x720"), "small": bad, "poster": bad}})
    assert x_videos.for_episode("x-1") == {"mp4": MP4.format("1280x720")}


@pytest.mark.parametrize("rows", [{}, {"x-1": None}, {"x-1": "a string"}, {"x-1": []}])
def test_a_video_with_no_row_or_a_broken_one_has_no_file(monkeypatch, rows):
    monkeypatch.setattr(x_videos, "_rows", lambda: rows)
    assert x_videos.for_episode("x-1") is None and x_videos.for_episode(None) is None
    hit = {"episode_id": "x-1", "start_seconds": 5}
    assert x_videos.attach(hit) is hit


def test_a_file_that_cannot_be_read_is_no_videos(tmp_path, monkeypatch):
    for text in (None, "{not json", "[1, 2]"):
        path = tmp_path / "x_videos.json"
        if text is not None:
            path.write_text(text)
        monkeypatch.setattr(x_videos, "PATH", path)
        x_videos._rows.cache_clear()
        assert x_videos.for_episode("x-1") is None
    x_videos._rows.cache_clear()


def test_a_passage_from_one_of_his_videos_carries_its_file(monkeypatch):
    monkeypatch.setattr(x_videos, "_rows", lambda: {"x-1": {"mp4": MP4.format("1280x720")}})
    hit = {"episode_id": "x-1", "start_seconds": 406, "deep_link": "https://x.com/a/status/1?t=406"}
    sent = x_videos.attach(hit)
    assert sent["file"] == {"mp4": MP4.format("1280x720")} and sent["start_seconds"] == 406
    assert "file" not in hit                                   # the passage itself is left alone
    assert "x_videos.attach(youtube_first(h.model_dump()))" in Path(main.__file__).read_text()


# --- the episode list -----------------------------------------------------------

OWN = {"episode_id": "x-2115418459765113280", "title": "Ansem on X: Q4 market thoughts",
       "url": "https://x.com/blknoiz06/status/2115418459765113280", "published_at": "2026-10-28",
       "show": "Ansem on X", "summary": "**TL;DR:** His own video.\n\n- [0:01:40] Gold."}


def test_his_own_video_is_sent_with_its_file(monkeypatch):
    file = {"mp4": MP4.format("1280x720"), "small": MP4.format("640x360")}
    monkeypatch.setattr(x_videos, "_rows", lambda: {OWN["episode_id"]: file})
    sent = main._episode_row(OWN, {}, {})
    assert sent["own"] is True and sent["file"] == file
    assert sent["blocks"] == [{"kind": "li", "text": "Gold.", "t": 100}]


def test_a_broadcast_is_never_sent_one_whatever_the_file_says(monkeypatch):
    """A broadcast has a player on X that opens at a second. Only a
    video he posted needs its file played."""
    live = dict(OWN, show="", episode_id="x-2118457515969032365")
    monkeypatch.setattr(x_videos, "_rows", lambda: {live["episode_id"]: {"mp4": MP4.format("1280x720")}})
    assert "file" not in main._episode_row(live, {}, {})
    monkeypatch.setattr(x_videos, "_rows", lambda: {})
    assert "file" not in main._episode_row(OWN, {}, {})


# --- the file that ships ----------------------------------------------------------

def test_every_row_that_ships_is_one_of_his_videos_and_playable():
    x_videos._rows.cache_clear()
    rows = json.loads(x_videos.PATH.read_text())
    own = {r["episode_id"] for r in load_shipped().values() if his_own(r.get("show"))}
    assert len(rows) >= 4 and set(rows) <= own
    for episode, row in rows.items():
        if "mp4" not in row:
            # Looked for and not found: written down so it is not asked
            # for every morning, and never handed to a page.
            assert set(row) <= {"none", "failed"} and x_videos.for_episode(episode) is None
            continue
        assert x_videos.for_episode(episode) == row, episode
        assert "/vid/avc1/" in row["mp4"] and row["small"] and row["poster"]
    # The four that were his when this was built, so it is seen if one goes.
    for episode in ("x-2116220650058846455", "x-2116134237971915020",
                    "x-2115752920779989271", "x-2115418459765113280"):
        assert episode in rows, episode


# --- the script that finds them -----------------------------------------------------

@pytest.fixture
def finder(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_x_videos
    monkeypatch.setattr(x_videos, "PATH", tmp_path / "x_videos.json")
    monkeypatch.setattr(find_x_videos, "own_videos", lambda: {
        "x-1": "https://x.com/blknoiz06/status/1", "x-2": "https://x.com/blknoiz06/status/2"})
    return find_x_videos


def test_it_looks_up_only_what_is_not_on_file(finder, monkeypatch):
    x_videos.PATH.write_text(json.dumps({"x-1": {"mp4": "kept"}, "x-gone": {"mp4": "old"}}))
    asked = []

    def look_up(url):
        asked.append(url)
        return {"mp4": MP4.format("1280x720")}
    monkeypatch.setattr(finder, "look_up", look_up)
    monkeypatch.setattr("sys.argv", ["find_x_videos.py"])
    assert finder.main() == 0
    assert asked == ["https://x.com/blknoiz06/status/2"]
    # One that is no longer his is dropped; one already found is not asked for again.
    assert json.loads(x_videos.PATH.read_text()) == {
        "x-1": {"mp4": "kept"}, "x-2": {"mp4": MP4.format("1280x720")}}


def test_a_video_it_cannot_find_ends_the_run_on_1_and_keeps_the_rest(finder, monkeypatch):
    def look_up(url):
        if url.endswith("/1"):
            raise RuntimeError("X said no")
        return None
    monkeypatch.setattr(finder, "look_up", look_up)
    monkeypatch.setattr("sys.argv", ["find_x_videos.py", "--all"])
    assert finder.main() == 1
    rows = json.loads(x_videos.PATH.read_text())
    assert rows["x-1"] == {"failed": 1} and set(rows["x-2"]) == {"none"}
    assert x_videos.for_episode("x-1") is None and x_videos.for_episode("x-2") is None
    assert not x_videos.PATH.with_suffix(".json.tmp").exists()


def test_a_post_that_keeps_failing_is_left_after_three_tries(finder, monkeypatch):
    """A deleted post would otherwise fail the job twice a day for good,
    and a job that always ends on 1 hides the morning it matters."""
    asked = []

    def look_up(url):
        asked.append(url)
        if url.endswith("/1"):
            raise RuntimeError("gone")
        return {"mp4": MP4.format("1280x720")}
    monkeypatch.setattr(finder, "look_up", look_up)
    monkeypatch.setattr("sys.argv", ["find_x_videos.py"])
    assert [finder.main() for _ in range(5)] == [1, 1, 1, 0, 0]
    assert asked.count("https://x.com/blknoiz06/status/1") == 3
    assert asked.count("https://x.com/blknoiz06/status/2") == 1
    assert json.loads(x_videos.PATH.read_text())["x-1"] == {"failed": 3}
    # Asked for by name, it is looked up again.
    monkeypatch.setattr("sys.argv", ["find_x_videos.py", "--episode", "x-1"])
    assert finder.main() == 1 and asked.count("https://x.com/blknoiz06/status/1") == 4


def test_a_post_with_no_file_to_find_is_asked_for_once(finder, monkeypatch):
    asked = []
    monkeypatch.setattr(finder, "look_up", lambda url: asked.append(url))
    monkeypatch.setattr("sys.argv", ["find_x_videos.py"])
    assert [finder.main() for _ in range(3)] == [1, 0, 0]
    assert len(asked) == 2                                     # the two posts, once each


def test_summaries_that_cannot_be_read_do_not_empty_the_file(finder, monkeypatch):
    """own_videos() is {} when the summaries are missing or broken. That
    used to prune every row, save {} and end on 0, and the morning job
    would have pushed it: none of his videos would play."""
    kept = {"x-1": {"mp4": MP4.format("1280x720")}}
    x_videos.PATH.write_text(json.dumps(kept))
    monkeypatch.setattr(finder, "own_videos", lambda: {})
    monkeypatch.setattr(finder, "look_up", lambda url: pytest.fail("nothing to look up"))
    monkeypatch.setattr("sys.argv", ["find_x_videos.py", "--all"])
    assert finder.main() == 1
    assert json.loads(x_videos.PATH.read_text()) == kept


def test_the_real_list_is_his_videos_and_no_broadcast():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_x_videos
    posts = find_x_videos.own_videos()
    assert len(posts) >= 4
    assert all(re.fullmatch(r"https://x\.com/\w+/status/\d+", url) for url in posts.values())


def test_the_morning_job_finds_new_ones_after_the_summaries_say_which_are_his():
    job = (ROOT / "scripts" / "daily_channels_check.sh").read_text()
    assert job.index("scripts/pack_summaries.py") < job.index("scripts/find_x_videos.py")
    assert "data/x_recordings.json data/x_copies.json data/x_videos.json" in job


# --- the page -------------------------------------------------------------------------

def test_the_player_asks_from_a_frame_that_names_no_site():
    frame = re.search(r"var FRAME='(.*?)';\n", PAGE, re.S).group(1)
    assert '<meta name="referrer" content="no-referrer">' in frame
    assert "<video controls playsinline" in frame and "<script" not in frame
    assert 'f.setAttribute("referrerpolicy","no-referrer")' in PAGE
    assert "f.srcdoc=FRAME;" in PAGE


def test_the_rest_of_the_page_still_says_where_it_is():
    """YouTube's player refuses to play when it is not told where it is
    embedded, so the whole page cannot go quiet: only the frame does."""
    assert PAGE.count('name="referrer"') == 1                  # the frame's, and no other
    assert "<head>" in PAGE and 'name="referrer"' not in PAGE.split("</head>", 1)[0]
    assert '"Referrer-Policy": "strict-origin-when-cross-origin"' in Path(main.__file__).read_text()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_page_plays_only_what_the_server_would_send():
    """The page checks the address again. Its pattern and the server's
    are held to each other on the same good and bad addresses."""
    literal = re.search(r"var FILE=(/.*?/);\n", PAGE).group(1)
    poster = re.search(r"var POSTER=(/.*?/);\n", PAGE).group(1)
    urls = [MP4.format("1280x720"), MP4.format("640x360").replace("?tag=29", ""),
            "https://video.twimg.com.evil.example/a.mp4", "http://video.twimg.com/a.mp4",
            "https://video.twimg.com/a.mp4?tag=1&x=1", "https://video.twimg.com/a.m3u8",
            "javascript:alert(1)", "https://video.twimg.com/a b.mp4", POSTER]
    out = subprocess.run([NODE, "-e", f"const F={literal},P={poster};"
                          "const u=JSON.parse(process.argv[1]);"
                          "console.log(JSON.stringify([u.map(x=>F.test(x)),u.map(x=>P.test(x))]))",
                          json.dumps(urls)], capture_output=True, text=True, check=True, timeout=30)
    files, posters = json.loads(out.stdout)
    assert files == [bool(x_videos.FILE.fullmatch(u)) for u in urls]
    assert posters == [bool(x_videos.POSTER.fullmatch(u)) for u in urls]
    assert files[:2] == [True, True] and not any(files[2:]) and posters[-1] is True


def test_every_place_a_time_can_be_pressed_plays_his_video_there():
    # a time in its summary, the summary opened, a passage, a time cited in the answer
    assert "else playFile(box,file,at,true,named,ep.url);" in PAGE
    assert 'playFile(box,fileOf(ep),0,auto,"Playing "+(ep.title||"this video"),ep.url);' in PAGE
    assert "(canEmbed(h.deep_link)||fileOf(h))" in PAGE
    assert 'playFile(slot.parentNode,fileOf(h),h.start_seconds,true,"the cited moment",h.deep_link);' in PAGE
    assert 'playFile(answer,fileOf(from),cited,true,"the cited moment",from.deep_link);' in PAGE
    assert "+ '\" data-i=\"' + lastHits.indexOf(hit)" in PAGE


def test_another_time_in_the_same_video_moves_the_player_it_has():
    play = PAGE[PAGE.index("function playFile("):][:1100]
    assert "here._video.currentTime=sec;" in play
    assert play.index("here._video.currentTime=sec;") < play.index("quiet();")


def test_the_time_pressed_last_is_the_one_a_slow_video_starts_at():
    """Pressed before the video had arrived, the second time was undone
    by the first one's handler when it did."""
    play = PAGE[PAGE.index("function playFile("):][:2600]
    assert "here._want=sec;" in play and "f._file=file.mp4; f._want=sec;" in play
    assert "if(f._want) video.currentTime=f._want;" in play
    assert "if(sec) video.currentTime=sec;" not in play
    assert "here._video.readyState>0" in play


def test_a_passage_finds_its_file_when_the_search_did_not_send_one():
    """The search that answers in one piece sends no file. The page has
    the episodes, each with its own."""
    look = PAGE[PAGE.index("function fileOf("):][:520]
    assert "EPS.filter(function(e){ return e.episode_id===thing.episode_id; })[0]" in look
    assert "FILE.test(f.mp4)" in look


def test_a_player_scrolled_back_to_clears_the_bar_a_phone_pins():
    assert ".pw .player{scroll-margin-top:76px}" in PAGE


def test_when_x_refuses_the_reader_is_told_and_offered_the_post():
    assert 'video.addEventListener("error",function(){ refused(slot,link); });' in PAGE
    told = PAGE[PAGE.index("function refused("):][:700]
    assert "X would not let this play here." in told and "Watch it on X" in told
    assert "a.href=safeUrl(link)" in told and "innerHTML=\"\"" in told


def test_a_cited_time_that_is_only_on_x_opens_there_instead_of_doing_nothing():
    cited = PAGE[PAGE.index("// One player for the answer's own citations"):][:2200]
    assert 'url.replace(/([?&]t=)\\d+s?/, "$1" + cited)' in cited
    assert 'window.open(there,"_blank","noopener")' in cited


def test_the_endpoint_sends_the_file_only_inside_a_row(monkeypatch):
    monkeypatch.setattr(main, "_listed", lambda r: r)
    monkeypatch.setattr(main, "_episodes_by_id", lambda: {})
    monkeypatch.setattr(main, "_json_object", lambda path: {})
    monkeypatch.setattr(x_videos, "_rows", lambda: {OWN["episode_id"]: {"mp4": MP4.format("1280x720")}})

    class Summaries:
        async def list_all(self):
            return [OWN]
    sent = asyncio.run(main.podcast_episodes(Summaries()))
    assert sent[0]["file"] == {"mp4": MP4.format("1280x720")} and sent[0]["youtube"] == ""

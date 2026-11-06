"""scripts/place_x_clips.py: reading X's captions, picking the copy of the
show to point at, and finding exactly where a clip begins and ends.

The episode here is made of words that each occur once, so where a clip
starts and stops is known to the word and the tests can say so exactly.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import place_x_clips as place  # noqa: E402
import pytest  # noqa: E402

# 500 words, ten to a segment, a segment every four seconds.
WORDS = [f"w{i}" for i in range(500)]
EPISODE = {"episode_id": "x-2115904746355851479", "title": "Episode 24",
           "segments": [{"t": 4.0 * n, "text": " ".join(WORDS[n * 10:n * 10 + 10])}
                        for n in range(50)]}
YOUTUBE = {**EPISODE, "episode_id": "KcazozbZHd0", "title": "the YouTube cut"}
ELSEWHERE = {"episode_id": "x-1", "title": "another show",
             "segments": [{"t": 0.0, "text": "nothing like it at all"}]}

# A clip that starts and stops in the middle of segments: words 103 to 157.
CLIP = " ".join(WORDS[103:158])

# MCG Live's daily show on YouTube, paired with its X broadcast, and the
# interview MCG cut from it and uploaded on its own: the same words twice.
DAILY = {**EPISODE, "episode_id": "XB71vFeU2zA", "title": "LIVE: Largest Crypto Hack Ever",
         "archive": "mcg"}
CUT = {**EPISODE, "episode_id": "zqOYgV9pXxI", "title": "Former Robinhood Employee",
       "archive": "mcg"}


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """No test here reads Pinecone: MCG Live's and ThreadGuy's shelves are
    empty unless a test writes one, and a read of the index fails loudly."""
    monkeypatch.setattr(place, "SHOWS", {"mcg": tmp_path / "no_mcg.json",
                                         "threadguy": tmp_path / "no_tg.json"})
    monkeypatch.setattr(place, "PAIRS", {"mcg": tmp_path / "no_mcg_pairs.json"})
    monkeypatch.setattr(place.local_cache, "LINES", tmp_path / "lines")

    def no_reads(*args):
        raise AssertionError("read the index in a test")
    monkeypatch.setattr(place.mcg_transcript, "rebuild", no_reads)


# X's own track for a real clip, 31 Oct 2026, shortened.
VTT = """WEBVTT

00:00:00.180 --> 00:00:01.640
<X-word-ms ms=20,60,160 index=1 character_ranges=0-0,1-3,4-9>I do think</X-word-ms>

00:00:01.680 --> 00:00:02.680
<X-word-ms ms=999 index=2 character_ranges=0-3>Izzy</X-word-ms>

NOTE a comment

3
00:00:01.960 --> 00:00:03.561
that's the trickiest part
"""


def test_captions_are_read_as_their_words_alone():
    assert place.caption_text(VTT) == "I do think Izzy that's the trickiest part"


def test_the_copy_on_x_is_pointed_at_over_the_youtube_one():
    found = [{"episode_id": "KcazozbZHd0", "matches": 167},
             {"episode_id": "x-2113369914467864972", "matches": 157}]
    assert place.choose(found)["episode_id"] == "x-2113369914467864972"
    assert place.choose(found[:1])["episode_id"] == "KcazozbZHd0"
    assert place.choose([]) is None


def test_the_show_that_aired_on_x_is_pointed_at_over_a_cut_of_it():
    found = [{"episode_id": "zqOYgV9pXxI", "matches": 200},
             {"episode_id": "XB71vFeU2zA", "matches": 150}]
    assert place.choose(found, {"XB71vFeU2zA"})["episode_id"] == "XB71vFeU2zA"
    assert place.choose(found)["episode_id"] == "zqOYgV9pXxI", "neither on X: the strongest"
    on_x = found + [{"episode_id": "x-2116199181912285508", "matches": 90}]
    assert place.choose(on_x, {"XB71vFeU2zA"})["episode_id"] == "x-2116199181912285508"


def test_a_clip_begins_and_ends_on_its_own_words():
    where = place.context(EPISODE, CLIP, start=40.0, duration=22.0)
    assert (where["start"], where["end"]) == (40, 60)
    # Right after the clip's last word, inside the segment it cut.
    assert where["after"].split()[0] == "w158"
    assert where["after_at"] == 60
    assert where["before"].split()[-1] == "w102"


def test_before_and_after_keep_to_their_limits():
    where = place.context(EPISODE, CLIP, start=40.0, duration=22.0)
    before, after = where["before"].split(), where["after"].split()
    assert len(before) <= place.BEFORE_WORDS and len(after) <= place.AFTER_WORDS
    # No word from more than BEFORE_SECONDS ahead of the clip.
    assert int(before[0][1:]) >= 10 * ((40 - place.BEFORE_SECONDS) // 4)
    # None from further than AFTER_SECONDS past its end.
    assert int(after[-1][1:]) < 10 * ((60 + place.AFTER_SECONDS) // 4 + 1)


def test_a_clip_at_the_very_end_has_nothing_after_it():
    tail = " ".join(WORDS[440:500])
    where = place.context(EPISODE, tail, start=176.0, duration=24.0)
    assert where["after"] == "" and where["after_at"] is None


def _got(**over):
    return {"duration": 22.0, "account": "blknoiz06", "broadcast": False,
            "caption": CLIP, **over}


@pytest.mark.parametrize("got,said", [
    ({"error": "X would not give the post: 404"}, "X would not give the post"),
    (_got(account="someclipper"), "not one of the show's accounts"),
    (_got(account=""), "not one of the show's accounts"),
    (_got(broadcast=True), "a broadcast"),
    (_got(duration=None), "no length"),
    (_got(duration=5 * 3600.0), "300 minutes long"),
    (_got(caption=""), "no captions on X"),
    (_got(caption="nothing in the archive sounds anything like this " * 3), "not in the archive"),
])
def test_what_is_not_a_placeable_clip_is_left_out_with_its_reason(got, said):
    entry, why = place.place_one("2116428709150478840", got, [EPISODE, ELSEWHERE])
    assert entry is None and said in why


def test_a_clip_is_placed_with_where_it_sits_and_both_sides():
    entry, why = place.place_one("2116428709150478840", _got(), [YOUTUBE, ELSEWHERE, EPISODE])
    assert entry["episode_id"] == "x-2115904746355851479", "the X copy, not YouTube's"
    assert entry["copies"] == 2 and entry["archive"] == "podcast"
    assert (entry["start"], entry["end"], entry["after_at"]) == (40, 60, 60)
    assert entry["account"] == "blknoiz06" and "0:00:40" in why


def test_an_mcg_clip_is_placed_on_the_show_that_aired_on_x():
    entry, why = place.place_one("2116528233193886171", _got(account="MCGlive"),
                                 [CUT, DAILY, ELSEWHERE], {"XB71vFeU2zA"})
    assert entry["archive"] == "mcg" and entry["episode_id"] == "XB71vFeU2zA"
    assert entry["copies"] == 2 and entry["account"] == "MCGlive"
    assert (entry["start"], entry["end"]) == (40, 60)


def test_a_long_cut_of_a_show_is_placed_like_a_clip():
    """MCG uploads each interview from its daily show, and Ansem posted 34
    minutes of Episode 24 as "Market Bubble ep.25": cuts, at any length."""
    entry, why = place.place_one("2117423871154589732", _got(duration=2046.8),
                                 [YOUTUBE, ELSEWHERE, EPISODE])
    assert entry["episode_id"] == "x-2115904746355851479" and "0:00:40" in why


def test_a_long_upload_stitched_from_parts_of_a_show_is_placed_without_its_clock():
    # The same words a line a minute: 50 minutes of show. Two pieces 40
    # minutes apart, cut into a 12-minute upload.
    long_show = {**EPISODE, "segments": [{**seg, "t": 60.0 * n}
                                         for n, seg in enumerate(EPISODE["segments"])]}
    stitched = " ".join(WORDS[0:60] + WORDS[400:460])
    entry, why = place.place_one("2115681447856030045", _got(duration=720.0, caption=stitched),
                                 [long_show, ELSEWHERE])
    assert entry["stitched"] is True and "stitched from several parts" in why
    assert (entry["start"], entry["end"]) == (0, 2700)
    short, why = place.place_one("2115681447856030045", _got(duration=48.0, caption=stitched),
                                 [long_show, ELSEWHERE])
    assert short is None and "not in the archive" in why, "a short clip is one stretch or nothing"


def test_a_long_video_sharing_a_moment_with_a_show_is_not_a_cut_of_it():
    """Ansem's own 37-minute video and ThreadGuy's stream both played the
    Hyperliquid announcement: 25 runs in common, and nothing else."""
    own = " ".join([f"own{i}" for i in range(5000)] + WORDS[100:140])
    entry, why = place.place_one("2116134237971915020", _got(duration=2205.7, caption=own),
                                 [EPISODE, ELSEWHERE])
    assert entry is None and "not in the archive" in why
    assert place.place_one("2116134237971915020", _got(duration=95.0, caption=" ".join(WORDS[100:140])),
                           [EPISODE])[0] is not None, "a short clip of that moment still is one"


def test_a_recording_is_never_placed_as_a_clip_of_itself():
    itself = {**EPISODE, "episode_id": "x-2116220650058846455"}
    entry, why = place.place_one("2116220650058846455", _got(duration=980.0), [itself])
    assert entry is None and "indexed as itself" in why


def test_what_is_not_a_clip_never_costs_a_read_of_the_index():
    def shows():
        raise AssertionError("asked for the shows")
    for got in ({"error": "X would not give the post: 404"}, _got(account="someclipper"),
                _got(broadcast=True), _got(duration=5 * 3600.0), _got(caption="")):
        entry, _ = place.place_one("2116528233193886171", got, shows)
        assert entry is None


def test_a_post_id_says_the_day_it_went_up():
    assert place.posted_on("2116199181912285508").isoformat() == "2026-10-30"
    assert place.posted_on("2117301468357177471").isoformat() == "2026-11-02"


def _shelf(tmp_path, monkeypatch, rows, pairs=None):
    (tmp_path / "mcg.json").write_text(json.dumps(rows))
    (tmp_path / "mcg_pairs.json").write_text(json.dumps(pairs or {}))
    monkeypatch.setattr(place, "SHOWS", {"mcg": tmp_path / "mcg.json"})
    monkeypatch.setattr(place, "PAIRS", {"mcg": tmp_path / "mcg_pairs.json"})


def test_only_the_week_before_a_clip_is_read_and_each_show_only_once(tmp_path, monkeypatch):
    _shelf(tmp_path, monkeypatch, [
        {"id": "XB71vFeU2zA", "title": "30 Oct", "published_at": "2026-10-30"},
        {"id": "oldShow0001", "title": "a month back", "published_at": "2026-10-01"},
        {"id": "nextWeek001", "title": "after the clip", "published_at": "2026-11-09"}],
        {"XB71vFeU2zA": {"broadcast": "https://x.com/i/broadcasts/1oKMvNPOzejGQ",
                         "offset": -2, "post": "2116199181912285508"},
         "unclocked01": {"broadcast": "https://x.com/i/broadcasts/1abc"}})
    read = []

    def rebuild(index, namespace, dimension, video):
        read.append(video)
        return DAILY["segments"]
    monkeypatch.setattr(place.mcg_transcript, "rebuild", rebuild)
    monkeypatch.setattr(place.Shows, "_read",
                        lambda self, archive, video: rebuild(None, "", 0, video))
    shows = place.Shows()
    assert shows.paired == {"XB71vFeU2zA"}, "paired means a broadcast and a clock"
    got = shows.before(place.posted_on("2116528233193886171"))           # 31 Oct
    assert [(e["episode_id"], e["archive"]) for e in got] == [("XB71vFeU2zA", "mcg")]
    shows.before(place.posted_on("2116528233193886171"))
    assert read == ["XB71vFeU2zA"], "read once a run"
    again = place.Shows().before(place.posted_on("2116528233193886171"))
    assert read == ["XB71vFeU2zA"], "and kept on this Mac after that"
    assert again[0]["segments"] == DAILY["segments"]


def test_a_show_that_cannot_be_read_is_not_kept_and_takes_no_placement_away(tmp_path, monkeypatch):
    _shelf(tmp_path, monkeypatch, [
        {"id": "XB71vFeU2zA", "title": "30 Oct", "published_at": "2026-10-30"}])

    def down(self, archive, video):
        raise ConnectionError("pinecone is down, key=pcsk_secret")
    monkeypatch.setattr(place.Shows, "_read", down)
    out = tmp_path / "x_clips.json"
    out.write_text(json.dumps({"2116528233193886171": {"old": True}}))
    monkeypatch.setattr(place, "OUT", out)
    monkeypatch.setattr(place, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(place, "fetch", lambda post, workdir: _got(account="MCGlive",
                                                                     caption="not ours " * 9))
    monkeypatch.setattr(place.episode_store, "load", lambda: [EPISODE])
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", "2116528233193886171"])
    place.main()
    assert json.loads(out.read_text()) == {"2116528233193886171": {"old": True}}
    assert place.local_cache.load_lines("mcg", "XB71vFeU2zA") is None


def test_a_run_keeps_old_placements_through_a_failed_download(tmp_path, monkeypatch):
    out = tmp_path / "x_clips.json"
    out.write_text(json.dumps({"2000000000000000001": {"old": True},
                               "2000000000000000002": {"old": True}}))
    replies = {"2000000000000000001": {"error": "X would not give the post: timeout"},
               "2000000000000000002": _got(caption="not ours at all " * 5),
               "2000000000000000003": _got()}
    monkeypatch.setattr(place, "OUT", out)
    monkeypatch.setattr(place, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(place, "fetch", lambda post, workdir: replies[post])
    monkeypatch.setattr(place.episode_store, "load", lambda: [EPISODE])
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", *replies])
    place.main()
    clips = json.loads(out.read_text())
    assert clips["2000000000000000001"] == {"old": True}, "a failed download proves nothing"
    assert "2000000000000000002" not in clips, "no longer placeable, so no longer shown"
    assert clips["2000000000000000003"]["start"] == 40
    assert list(clips) == sorted(clips)


def test_only_post_ids_are_taken(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", "123456789", "../etc/passwd"])
    with pytest.raises(SystemExit, match="not post ids"):
        place.main()


def test_the_clip_accounts_are_read_without_regard_to_case():
    from app import x_posts
    for handle in ("blknoiz06", "MarketBubble", "mbubbleSearch", "Kaiz_294", "KAIZ_294",
                   "MCGlive", "notthreadguy", "OrderBookShow", "BullClips", "insentosprime"):
        assert x_posts.clip_account(handle), handle
    for handle in ("kaiz294", "someclipper", "", None, 7):
        assert not x_posts.clip_account(handle), handle


def test_the_search_asks_only_for_their_own_videos():
    q = place.search_query(frozenset({"blknoiz06", "kaiz_294"}))
    assert q == "(from:blknoiz06 OR from:kaiz_294) has:videos -is:retweet -is:reply"


def test_a_search_run_places_what_it_found_and_remembers_how_far_it_read(tmp_path, monkeypatch):
    out, searched = tmp_path / "x_clips.json", tmp_path / "x_clips_search.json"
    searched.write_text(json.dumps({"newest": "2000000000000000001"}))
    asked = []

    async def fake_search(since_id):
        asked.append(since_id)
        return ["2000000000000000003", "2000000000000000002"]
    monkeypatch.setattr(place, "OUT", out)
    monkeypatch.setattr(place, "SEARCHED", searched)
    monkeypatch.setattr(place, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(place, "search", fake_search)
    monkeypatch.setattr(place, "fetch", lambda post, workdir: _got(account="Kaiz_294"))
    monkeypatch.setattr(place.episode_store, "load", lambda: [EPISODE])
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", "--search"])
    place.main()
    assert asked == ["2000000000000000001"], "only what came after the newest read"
    assert sorted(json.loads(out.read_text())) == ["2000000000000000002", "2000000000000000003"]
    assert json.loads(searched.read_text()) == {"newest": "2000000000000000003"}


def test_a_search_that_finds_nothing_keeps_its_place(tmp_path, monkeypatch):
    out, searched = tmp_path / "x_clips.json", tmp_path / "x_clips_search.json"
    searched.write_text(json.dumps({"newest": "2000000000000000009"}))

    async def nothing(since_id):
        return []
    monkeypatch.setattr(place, "OUT", out)
    monkeypatch.setattr(place, "SEARCHED", searched)
    monkeypatch.setattr(place, "search", nothing)
    monkeypatch.setattr(place.episode_store, "load", lambda: [EPISODE])
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", "--search"])
    place.main()
    assert json.loads(searched.read_text()) == {"newest": "2000000000000000009"}


def test_a_clip_without_captions_is_heard_only_when_it_is_worth_it():
    assert place.needs_ears(_got(caption=""))
    assert place.needs_ears(_got(caption="", duration=2729.0)), "a long cut, heard too"
    for got in (_got(), _got(caption="", duration=5 * 3600.0), _got(caption="", broadcast=True),
                _got(caption="", account="someclipper"), _got(caption="", duration=None),
                {"error": "x"}):
        assert not place.needs_ears(got), got


def test_a_heard_clip_is_placed_and_says_so(tmp_path, monkeypatch):
    out = tmp_path / "x_clips.json"
    monkeypatch.setattr(place, "OUT", out)
    monkeypatch.setattr(place, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(place, "fetch", lambda post, workdir: _got(caption="", account="mbubbleSearch"))
    monkeypatch.setattr(place, "listen", lambda post, workdir, seconds=None: CLIP)
    monkeypatch.setattr(place.episode_store, "load", lambda: [EPISODE])
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", "2000000000000000005"])
    place.main()
    entry = json.loads(out.read_text())["2000000000000000005"]
    assert entry["heard_from"] == "audio" and entry["start"] == 40


def test_x_not_answering_costs_one_post_not_the_run(monkeypatch, tmp_path):
    """3 Nov 2026: a 40-minute upload's audio took longer than the five
    minutes allowed, and the uncaught timeout ended the run before it wrote
    the twelve clips it had placed."""
    import subprocess

    asked = []

    def slow(cmd, **kw):
        asked.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(place.subprocess, "run", slow)
    assert "no answer" in place.fetch("2115537742235152750", tmp_path)["error"]
    assert place.listen("2115537742235152750", tmp_path, 2400.0) == ""
    assert asked == [180, 2700], "the download gets the video's own length on top"


class _Ran:
    def __init__(self, stdout):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


def test_a_broadcast_that_comes_back_without_its_account_is_named_by_the_embed(monkeypatch, tmp_path):
    """6 Nov 2026: X sent Market Bubble Episode 25's broadcast with no
    account. yt-dlp printed NA, which passed for a handle, and the show was
    left as somebody else's."""
    asked = []
    monkeypatch.setattr(place.subprocess, "run", lambda cmd, **kw: _Ran("\t\ttwitter:broadcast\n"))
    monkeypatch.setattr(place, "author", lambda post: asked.append(post) or "MarketBubble")
    got = place.fetch("2118457515969032365", tmp_path)
    assert got["account"] == "MarketBubble" and got["broadcast"] and got["duration"] is None
    assert asked == ["2118457515969032365"]


def test_the_embed_is_only_asked_when_yt_dlp_gives_no_account(monkeypatch, tmp_path):
    monkeypatch.setattr(place.subprocess, "run", lambda cmd, **kw: _Ran("NA\tMarketBubble\ttwitter:broadcast\n"))
    monkeypatch.setattr(place, "author", lambda post: pytest.fail("not needed"))
    assert place.fetch("2115904746355851479", tmp_path)["account"] == "MarketBubble"


def test_yt_dlp_is_asked_for_nothing_rather_than_na_where_a_field_is_missing(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(place.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or _Ran("\t\tx\n"))
    monkeypatch.setattr(place, "author", lambda post: "")
    assert place.fetch("2118457515969032365", tmp_path)["account"] == ""
    assert "%(duration|)s\t%(uploader_id|)s\t%(extractor)s" in seen[0]


class _Reply:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("body,name", [
    (b'{"user": {"screen_name": "MarketBubble"}}', "MarketBubble"),
    (b'{"user": {"screen_name": "not a handle"}}', ""),
    (b'{"user": null}', ""),
    (b'[]', ""),
    (b'<html>rate limited</html>', ""),
])
def test_the_embed_names_the_author_or_nobody(monkeypatch, body, name):
    monkeypatch.setattr(place.urllib.request, "urlopen", lambda url, timeout: _Reply(body))
    assert place.author("2118457515969032365") == name


def test_the_embed_not_answering_is_nobody(monkeypatch):
    def down(url, timeout):
        raise OSError("no route")
    monkeypatch.setattr(place.urllib.request, "urlopen", down)
    assert place.author("2118457515969032365") == ""


def _search_world(tmp_path, monkeypatch, state, found, replies, episodes):
    out, searched = tmp_path / "x_clips.json", tmp_path / "x_clips_search.json"
    searched.write_text(json.dumps(state))
    read = []

    async def fake_search(since_id):
        return list(found)

    def fetch(post, workdir):
        read.append(post)
        return dict(replies[post])
    monkeypatch.setattr(place, "OUT", out)
    monkeypatch.setattr(place, "SEARCHED", searched)
    monkeypatch.setattr(place, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(place, "search", fake_search)
    monkeypatch.setattr(place, "fetch", fetch)
    monkeypatch.setattr(place.episode_store, "load", lambda: list(episodes))
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", "--search"])
    place.main()
    return read, json.loads(searched.read_text()), json.loads(out.read_text())


NEW_CLIP = "2117000000000000001"         # 3 Nov 2026
OTHER_SHOW = {**EPISODE, "episode_id": "x-2117000000000000099", "segments": []}


def test_his_clip_of_a_show_not_yet_indexed_waits_for_it(tmp_path, monkeypatch):
    """Two of Ansem's 30 Oct clips were refused because the video they were
    cut from went in three days later, and stayed refused until a hand run."""
    monkeypatch.setattr(place, "_today", lambda: date(2026, 11, 4))
    stranger = _got(caption="not ours at all " * 5)
    replies = {NEW_CLIP: stranger}
    read, state, clips = _search_world(tmp_path, monkeypatch, {"newest": "1"}, [NEW_CLIP],
                                       replies, [OTHER_SHOW])
    assert state["unplaced"] == {NEW_CLIP: {"held": 1}} and clips == {}

    read, state, _ = _search_world(tmp_path, monkeypatch, state, [], replies, [OTHER_SHOW])
    assert read == [], "the archive has not grown: not read again"

    replies[NEW_CLIP] = _got()
    read, state, clips = _search_world(tmp_path, monkeypatch, state, [], replies,
                                       [OTHER_SHOW, EPISODE])
    assert read == [NEW_CLIP] and clips[NEW_CLIP]["start"] == 40
    assert "unplaced" not in state and state["newest"] == NEW_CLIP


def test_only_the_shows_own_accounts_wait_and_only_for_a_week(tmp_path, monkeypatch):
    monkeypatch.setattr(place, "_today", lambda: date(2026, 11, 20))
    stranger = "not ours at all " * 5
    replies = {"2117000000000000002": _got(account="Kaiz_294", caption=stranger),
               "2117000000000000003": _got(caption=stranger)}
    _, state, _ = _search_world(
        tmp_path, monkeypatch,
        {"newest": "1", "unplaced": {NEW_CLIP: {"held": 0}}}, list(replies), replies, [OTHER_SHOW])
    assert state["unplaced"] == {"2117000000000000003": {"held": 1}}, \
        "Kaiz clips every show there is; a 3 Nov clip is past its week"


def test_a_failed_download_keeps_its_place_in_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(place, "_today", lambda: date(2026, 11, 4))
    replies = {NEW_CLIP: {"error": "X would not give the post: timeout"}}
    _, state, _ = _search_world(tmp_path, monkeypatch,
                                {"newest": "1", "unplaced": {NEW_CLIP: {"held": 0}}},
                                [], replies, [OTHER_SHOW])
    assert state["unplaced"] == {NEW_CLIP: {"held": 0}}

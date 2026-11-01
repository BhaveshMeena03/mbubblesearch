"""scripts/place_x_clips.py: reading X's captions, picking the copy of the
show to point at, and finding exactly where a clip begins and ends.

The episode here is made of words that each occur once, so where a clip
starts and stops is known to the word and the tests can say so exactly.
"""

from __future__ import annotations

import json
import sys
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
    (_got(duration=2729.0), "45 minutes long"),
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
                   "OrderBookShow", "BullClips", "insentosprime"):
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
    for got in (_got(), _got(caption="", duration=2729.0), _got(caption="", broadcast=True),
                _got(caption="", account="someclipper"), _got(caption="", duration=None),
                {"error": "x"}):
        assert not place.needs_ears(got), got


def test_a_heard_clip_is_placed_and_says_so(tmp_path, monkeypatch):
    out = tmp_path / "x_clips.json"
    monkeypatch.setattr(place, "OUT", out)
    monkeypatch.setattr(place, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(place, "fetch", lambda post, workdir: _got(caption="", account="mbubbleSearch"))
    monkeypatch.setattr(place, "listen", lambda post, workdir: CLIP)
    monkeypatch.setattr(place.episode_store, "load", lambda: [EPISODE])
    monkeypatch.setattr(sys, "argv", ["place_x_clips.py", "2000000000000000005"])
    place.main()
    entry = json.loads(out.read_text())["2000000000000000005"]
    assert entry["heard_from"] == "audio" and entry["start"] == 40

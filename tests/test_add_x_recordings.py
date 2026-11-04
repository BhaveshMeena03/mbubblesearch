"""scripts/add_x_recordings.py: which of Market Bubble's and Ansem's posts
get indexed unattended, how, and what happens when it fails.

Nothing here reads X, Pinecone or Groq: every reader is replaced.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import add_x_recordings as rec  # noqa: E402

EP24 = {"episode_id": "x-2115904746355851479", "title": "Episode 24",
        "url": "https://x.com/MarketBubble/status/2115904746355851479",
        "segments": [{"t": 4.0 * n, "text": " ".join(f"w{i}" for i in range(n * 10, n * 10 + 10))}
                     for n in range(50)]}
CUT = " ".join(f"w{i}" for i in range(103, 158))
ANSEM_COPY = "2115904745818996865"      # his broadcast of Episode 24, 0.13 s before theirs
MB_EP24 = "2115904746355851479"


def _got(**over):
    return {"duration": 1800.0, "account": "blknoiz06", "broadcast": False,
            "caption": "his own words, nothing the archive holds", **over}


@pytest.mark.parametrize("got,cut,on_air,watcher,what,why", [
    ({"error": "X would not give the post: timeout"}, None, False, False, "wait", "timeout"),
    (_got(account="kaiz_294"), None, False, False, "leave", "@kaiz_294"),
    (_got(duration=95.0), None, False, False, "leave", "a clip"),
    (_got(duration=None), None, False, False, "wait", "no length"),
    (_got(), {"episode_id": "x-2115904746355851479"}, False, False, "leave", "a cut of"),
    (_got(broadcast=True, account="MarketBubble", duration=None), None, True, False,
     "wait", "on air"),
    (_got(broadcast=True, account="MarketBubble", duration=None), None, False, True,
     "wait", "Friday watcher"),
    (_got(), None, False, True, "index", "not in the archive"),
    (_got(account="MarketBubble", duration=2400.0), None, False, False, "index", ""),
    (_got(broadcast=True, account="MarketBubble", duration=None), None, False, False, "index", ""),
])
def test_what_gets_indexed_unattended(got, cut, on_air, watcher, what, why):
    said = rec.verdict("2117423871154589732", got, set(), cut, on_air, watcher)
    assert said[0] == what and why in said[1]


def test_a_recording_already_held_is_left_alone():
    said = rec.verdict("2115904746355851479", _got(account="MarketBubble"),
                       {"x-2115904746355851479"}, None, False, False)
    assert said == ("leave", "already indexed")


def test_ansem_goes_in_as_his_own_show_and_market_bubble_without_labels():
    ansem = rec.command("2116220650058846455", _got())
    assert ansem[2:] == ["https://x.com/blknoiz06/status/2116220650058846455",
                         "--engine", "groq", "--show", "Ansem on X"]
    mb = rec.command("2117000000000000001", _got(account="MarketBubble"))
    assert mb[2:] == ["https://x.com/MarketBubble/status/2117000000000000001",
                      "--engine", "groq", "--force", "--no-labels"]


def test_a_long_upload_of_a_show_held_is_a_cut_not_a_recording(tmp_path):
    found = rec.cut_of("2117423871154589732", _got(caption=CUT), [EP24], tmp_path)
    assert found["episode_id"] == "x-2115904746355851479"
    assert rec.cut_of("2117423871154589732", _got(), [EP24], tmp_path) is None
    assert rec.cut_of("1", _got(broadcast=True, caption=CUT), [EP24], tmp_path) is None
    stitched = " ".join(f"w{i}" for i in [*range(0, 60), *range(400, 460)])
    assert rec.cut_of("2115681447856030045", _got(caption=stitched), [EP24],
                      tmp_path)["episode_id"] == "x-2115904746355851479", "stitched from parts"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A store that add_broadcast.py appends to, and a log of what ran."""
    store = [EP24]
    ran: list[list[str]] = []
    replies: dict[str, dict] = {}

    class Done:
        def __init__(self, code):
            self.returncode = code

    def run(cmd, **kw):
        ran.append(cmd)
        post = cmd[2].rsplit("/", 1)[-1]
        if replies[post].get("fails"):
            return Done(1)
        store.append({"episode_id": f"x-{post}", "title": "new", "segments": []})
        return Done(0)

    class NoShows:
        paired: set = set()

        def before(self, day):
            return []

    monkeypatch.setattr(rec, "STATE", tmp_path / "x_recordings.json")
    monkeypatch.setattr(rec, "COPIES", tmp_path / "x_copies.json")
    monkeypatch.setattr(rec.episode_store, "load", lambda: list(store))
    monkeypatch.setattr(rec.clips, "fetch", lambda post, workdir: dict(replies[post]))
    monkeypatch.setattr(rec.clips, "Shows", NoShows)
    monkeypatch.setattr(rec, "live_status", lambda post: "was_live")
    monkeypatch.setattr(rec, "watcher_running", lambda: False)
    monkeypatch.setattr(rec.subprocess, "run", run)
    heard: list[tuple[str, str]] = []

    def measure(copy, show):
        heard.append((copy, show))
        return replies[copy].get("clock")
    monkeypatch.setattr(rec.same_feed, "measure", measure)
    return {"store": store, "ran": ran, "replies": replies, "heard": heard,
            "state": tmp_path / "x_recordings.json", "copies": tmp_path / "x_copies.json"}


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["add_x_recordings.py", *argv])
    return rec.main()


def test_a_new_video_of_his_is_indexed_and_recorded(world, monkeypatch):
    world["replies"]["2116220650058846455"] = _got()
    world["replies"]["2117423871154589732"] = _got(caption=CUT)
    _main(monkeypatch, "2116220650058846455", "2117423871154589732")
    assert [c[2].rsplit("/", 1)[-1] for c in world["ran"]] == ["2116220650058846455"], \
        "the cut of Episode 24 is not indexed again"
    posts = json.loads(world["state"].read_text())["posts"]
    assert posts["2116220650058846455"]["what"] == "indexed"
    assert posts["2117423871154589732"]["what"] == "leave"


def test_a_failure_is_tried_on_the_next_runs_then_left_for_a_person(world, monkeypatch):
    world["replies"]["2116220650058846455"] = _got(fails=True)
    for n in (1, 2):
        _main(monkeypatch, "2116220650058846455")
        row = json.loads(world["state"].read_text())["posts"]["2116220650058846455"]
        assert (row["what"], row["tries"]) == ("wait", n)
    _main(monkeypatch, "2116220650058846455")
    row = json.loads(world["state"].read_text())["posts"]["2116220650058846455"]
    assert row["what"] == "failed" and "left for a person" in row["why"]


def test_a_search_run_retries_what_waited_and_remembers_how_far_it_read(world, monkeypatch):
    world["state"].write_text(json.dumps({"newest": "2117000000000000000", "posts": {
        "2116220650058846455": {"what": "wait", "tries": 1, "why": "still on air"}}}))
    world["replies"]["2116220650058846455"] = _got()
    world["replies"]["2117500000000000000"] = _got(duration=40.0)
    asked = []

    async def search(since_id, query):
        asked.append((since_id, query))
        return ["2117500000000000000"]
    monkeypatch.setattr(rec.clips, "search", search)
    _main(monkeypatch, "--search")
    state = json.loads(world["state"].read_text())
    assert asked == [("2117000000000000000", rec.QUERY)]
    assert state["newest"] == "2117500000000000000"
    assert state["posts"]["2116220650058846455"]["what"] == "indexed"
    assert state["posts"]["2117500000000000000"]["what"] == "leave"


def test_a_dry_run_indexes_nothing_and_writes_nothing(world, monkeypatch):
    world["replies"]["2116220650058846455"] = _got()
    _main(monkeypatch, "--dry-run", "2116220650058846455")
    assert world["ran"] == [] and not world["state"].exists()


def test_only_post_ids_are_taken(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["add_x_recordings.py", "1234567", "../etc/passwd"])
    with pytest.raises(SystemExit, match="not post ids"):
        rec.main()


def test_a_broadcast_that_will_not_say_it_has_ended_waits(monkeypatch):
    import subprocess

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(rec.subprocess, "run", slow)
    assert rec.live_status("2117301468357177471") in rec.ON_AIR


def test_his_own_video_that_played_a_moment_from_a_show_is_still_his(tmp_path):
    """2116134237971915020: 37 minutes of Ansem, with the Hyperliquid
    announcement ThreadGuy also played. Not a cut, so it gets indexed."""
    own = " ".join([f"own{i}" for i in range(5000)] + [f"w{i}" for i in range(100, 140)])
    assert rec.cut_of("2116134237971915020", _got(caption=own, duration=2205.7), [EP24],
                      tmp_path) is None


# --- Ansem's copy of each episode --------------------------------------------

def _copy(**over):
    return _got(broadcast=True, duration=None, **over)


def test_his_broadcast_that_went_up_with_theirs_is_its_twin():
    """23 episodes since May 2026, all within 40 seconds of each other."""
    shows = {MB_EP24, "2113369914467864972"}
    assert rec.twin_of(ANSEM_COPY, shows) == MB_EP24
    assert rec.twin_of("2116134237971915020", shows) is None, "his own video the next day"
    assert rec.twin_of(MB_EP24, {MB_EP24}) is None


def test_only_market_bubbles_own_broadcasts_can_be_a_twin():
    episodes = [EP24, {"episode_id": "x-2116134237971915020",
                       "url": "https://x.com/blknoiz06/status/2116134237971915020"}]
    state = {"posts": {"2117000000000000001": {"account": "MarketBubble", "what": "wait"},
                       "2117000000000000002": {"account": "MarketBubble", "what": "leave"},
                       "2117000000000000003": {"account": "blknoiz06", "what": "wait"}}}
    assert rec.market_bubble_shows(episodes, state) == {MB_EP24, "2117000000000000001"}


@pytest.mark.parametrize("got,twin,held,on_air,what,why", [
    (_copy(), MB_EP24, {f"x-{MB_EP24}"}, False, "copy", f"a copy of x-{MB_EP24}"),
    (_copy(), MB_EP24, set(), False, "wait", "not indexed yet"),
    (_copy(), MB_EP24, {f"x-{MB_EP24}"}, True, "wait", "on air"),
    (_copy(), None, set(), False, "index", ""),
    (_got(), MB_EP24, {f"x-{MB_EP24}"}, False, "index", ""),
    (_copy(account="MarketBubble"), MB_EP24, {f"x-{MB_EP24}"}, False, "index", ""),
])
def test_what_happens_to_his_broadcast_of_a_show(got, twin, held, on_air, what, why):
    said = rec.verdict(ANSEM_COPY, got, held, None, on_air, False, twin)
    assert said[0] == what and why in said[1]


def test_a_copy_already_clocked_is_left_alone():
    said = rec.verdict(ANSEM_COPY, _copy(), {f"x-{MB_EP24}"}, None, False, False, MB_EP24,
                       {ANSEM_COPY: {"episode_id": f"x-{MB_EP24}"}})
    assert said == ("leave", f"already a copy of x-{MB_EP24}")


def test_his_copy_is_clocked_and_never_indexed_again(world, monkeypatch):
    clock = {"broadcast": "https://x.com/i/broadcasts/1PJqrNvWzwexb", "offset": 0}
    world["replies"][ANSEM_COPY] = _copy(clock=clock)
    _main(monkeypatch, ANSEM_COPY)
    assert world["ran"] == [] and world["heard"] == [(ANSEM_COPY, MB_EP24)]
    row = json.loads(world["copies"].read_text())[ANSEM_COPY]
    assert row["episode_id"] == f"x-{MB_EP24}" and row["offset"] == 0
    assert row["broadcast"] == clock["broadcast"] and row["account"] == "blknoiz06"
    assert json.loads(world["state"].read_text())["posts"][ANSEM_COPY]["what"] == "copy"
    _main(monkeypatch, ANSEM_COPY)
    assert len(world["heard"]) == 1, "heard once"


def test_his_copy_found_before_their_post_in_one_run_waits_for_it(world, monkeypatch):
    """His id is the smaller one about half the time, so he is read first.
    Their post is indexed in the same run, and then his is its copy."""
    world["store"].clear()
    new_mb, new_copy = "2118400000000000000", "2118399999000000000"
    world["replies"][new_copy] = _copy(clock={"broadcast": "https://x.com/i/broadcasts/1a", "offset": 0})
    world["replies"][new_mb] = _copy(account="MarketBubble")
    _main(monkeypatch, new_copy, new_mb)
    assert [c[2].rsplit("/", 1)[-1] for c in world["ran"]] == [new_mb], "only theirs is indexed"
    assert world["heard"] == [(new_copy, new_mb)]
    posts = json.loads(world["state"].read_text())["posts"]
    assert posts[new_mb]["what"] == "indexed" and posts[new_copy]["what"] == "copy"


def test_a_copy_that_cannot_be_heard_is_tried_again(world, monkeypatch):
    world["replies"][ANSEM_COPY] = _copy(clock=None)
    _main(monkeypatch, ANSEM_COPY)
    row = json.loads(world["state"].read_text())["posts"][ANSEM_COPY]
    assert (row["what"], row["tries"]) == ("wait", 1) and "could not hear" in row["why"]
    assert not world["copies"].exists() and world["ran"] == []


def test_a_dry_run_hears_nothing(world, monkeypatch):
    world["replies"][ANSEM_COPY] = _copy(clock={"offset": 0})
    _main(monkeypatch, "--dry-run", ANSEM_COPY)
    assert world["heard"] == [] and not world["copies"].exists()

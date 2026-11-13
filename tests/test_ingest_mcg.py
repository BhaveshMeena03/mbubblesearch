"""The MCG ingest, and the two ways its first scheduled run failed quietly.

Both are the same shape of bug: the job went green having done nothing,
which from outside is indistinguishable from an archive already up to date.
"""
import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from scripts import ingest_mcg


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.delenv("YTDLP_PROXY", raising=False)


# --- the proxy the workflow passes and the script never read -------------

def test_no_proxy_configured_means_no_flag():
    assert ingest_mcg.proxy_args() == []


def test_a_configured_proxy_becomes_a_flag(monkeypatch):
    monkeypatch.setenv("YTDLP_PROXY", "http://gateway.example:7000")
    assert ingest_mcg.proxy_args() == ["--proxy", "http://gateway.example:7000"]


def test_the_proxy_is_read_at_call_time(monkeypatch):
    """Set by a shell after import, the way the runner does it."""
    assert ingest_mcg.proxy_args() == []
    monkeypatch.setenv("YTDLP_PROXY", "http://late.example:7000")
    assert ingest_mcg.proxy_args() == ["--proxy", "http://late.example:7000"]


def test_fetching_a_video_goes_through_the_proxy(monkeypatch, tmp_path):
    """The bug: YTDLP_PROXY was in the job's env and in nothing it ran.

    YouTube answered every download with "Sign in to confirm you're not a
    bot", all three episodes were caught, and the run exited green.
    """
    monkeypatch.setenv("YTDLP_PROXY", "http://gateway.example:7000")
    monkeypatch.setattr(ingest_mcg, "AUDIO_DIR", tmp_path)
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        # Write the file the real yt-dlp would leave behind.
        out = cmd[cmd.index("-o") + 1]
        with open(out, "wb") as fh:
            fh.write(b"\0" * 2_000_000)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ingest_mcg.subprocess, "run", fake_run)
    ingest_mcg.fetch_audio("abc123")

    assert seen, "nothing was run"
    assert "--proxy" in seen[0], "the download bypassed the proxy"
    assert seen[0][seen[0].index("--proxy") + 1] == "http://gateway.example:7000"


def test_the_date_lookup_also_goes_through_the_proxy(monkeypatch):
    """It hits the video page too, so it is refused the same way."""
    monkeypatch.setenv("YTDLP_PROXY", "http://gateway.example:7000")
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "20260921", "")

    monkeypatch.setattr(ingest_mcg.subprocess, "run", fake_run)
    assert ingest_mcg.published("abc123") == "2026-09-21"
    assert "--proxy" in seen[0]


def test_the_channel_listing_stays_unproxied(monkeypatch):
    """A quiet run should spend no proxy bandwidth.

    The listing is not refused from a datacentre; only fetching a video is.
    """
    monkeypatch.setenv("YTDLP_PROXY", "http://gateway.example:7000")
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '{"entries": []}', "")

    monkeypatch.setattr(ingest_mcg.subprocess, "run", fake_run)
    ingest_mcg.enumerate_tab("https://www.youtube.com/@MCG_live/videos", 5)
    assert "--proxy" not in seen[0], "a listing should not cost proxy traffic"


# --- chunking, where a wrong offset is silent and permanent --------------

def test_chunk_starts_are_the_real_seconds():
    """Each chunk's clock restarts at zero; the offset is what fixes it.

    Getting this wrong does not fail. It produces an episode whose every
    citation is minutes out, on a product whose whole claim is the second.
    """
    starts = [n * float(ingest_mcg.CHUNK_SECONDS) for n in range(4)]
    assert starts == [0.0, 900.0, 1800.0, 2700.0]


def test_a_chunk_fits_the_upload_cap():
    """16kHz mono at 32kbps: fifteen minutes is about 3.6MB, cap is 24MB."""
    bytes_per_second = 32_000 / 8
    assert ingest_mcg.CHUNK_SECONDS * bytes_per_second < 24 * 1024 * 1024


# --- an episode is shelved whole or not at all ----------------------------
#
# Fourteen ThreadGuy streams went onto the shelf with minutes, or nothing,
# transcribed: a chunk Groq refused six times came back as an empty list,
# and nothing checked the transcript reached the end of the recording.

class _Reply:
    def __init__(self, status, body=None, wait="1"):
        self.status_code = status
        self.headers = {"retry-after": wait}
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._body


def _one_chunk(monkeypatch, tmp_path, replies):
    import httpx
    chunk = tmp_path / "chunk0000.mp3"
    chunk.write_bytes(b"x")
    monkeypatch.setattr(ingest_mcg, "to_chunks",
                        lambda path, work: [(900.0, chunk)])
    monkeypatch.setattr(ingest_mcg.time, "sleep", lambda s: None)
    monkeypatch.setattr(ingest_mcg, "CHUNK_WORKERS", 1)
    calls = []

    def post(*a, **k):
        calls.append(1)
        return replies[min(len(calls), len(replies)) - 1]
    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_a_chunk_refused_every_time_fails_the_episode(monkeypatch, tmp_path):
    calls = _one_chunk(monkeypatch, tmp_path, [_Reply(429)])
    with pytest.raises(ingest_mcg.RateLimitedError):
        ingest_mcg.transcribe_via_groq(tmp_path / "a.m4a", "k")
    assert len(calls) == ingest_mcg.MAX_REFUSALS + 1


def test_a_refusal_then_an_answer_is_kept(monkeypatch, tmp_path):
    ok = _Reply(200, {"segments": [{"start": 3.0, "text": " gm "}]})
    _one_chunk(monkeypatch, tmp_path, [_Reply(429), _Reply(429), ok])
    got = ingest_mcg.transcribe_via_groq(tmp_path / "a.m4a", "k")
    assert got == [{"t": 903.0, "text": "gm"}], "on the episode clock"


def test_errors_still_give_up_after_six(monkeypatch, tmp_path):
    calls = _one_chunk(monkeypatch, tmp_path, [_Reply(500)])
    with pytest.raises(RuntimeError, match="transcribe failed"):
        ingest_mcg.transcribe_via_groq(tmp_path / "a.m4a", "k")
    assert len(calls) == 6


def test_a_failed_attempt_says_so_in_the_log(monkeypatch, tmp_path, capsys):
    ok = _Reply(200, {"segments": [{"start": 3.0, "text": " gm "}]})
    _one_chunk(monkeypatch, tmp_path, [_Reply(500), ok])
    ingest_mcg.transcribe_via_groq(tmp_path / "a.m4a", "k")
    assert "groq chunk0000.mp3 failed (RuntimeError: http 500) after 0s" in capsys.readouterr().out


@pytest.mark.parametrize("wall, awake, line", [
    # 13 Nov 2026: lid shut on battery, the answer came after two hours
    (7200.0, 600.0, "answered after 7200s, the mac was asleep for 6600s of it"),
    (700.0, 700.0, "answered after 700s\n"),     # awake the whole time: Groq was slow
])
def test_a_slow_answer_says_whether_the_mac_slept(monkeypatch, tmp_path, capsys,
                                                 wall, awake, line):
    ok = _Reply(200, {"segments": [{"start": 3.0, "text": " gm "}]})
    _one_chunk(monkeypatch, tmp_path, [ok])
    ticks = iter([(1000.0, 50.0)] + [(1000.0 + wall, 50.0 + awake)] * 2)
    monkeypatch.setattr(ingest_mcg, "_clock", lambda: next(ticks))
    assert ingest_mcg.transcribe_via_groq(tmp_path / "a.m4a", "k") == [{"t": 903.0, "text": "gm"}]
    assert line in capsys.readouterr().out


def test_a_quick_answer_adds_nothing_to_the_log(monkeypatch, tmp_path, capsys):
    _one_chunk(monkeypatch, tmp_path, [_Reply(200, {"segments": []})])
    ingest_mcg.transcribe_via_groq(tmp_path / "a.m4a", "k")
    assert "groq " not in capsys.readouterr().out


@pytest.mark.parametrize("last, seconds, short", [
    (None, 3600, True),       # nothing transcribed
    (360, 11220, True),       # six minutes of a 187 minute stream
    (7200, 11220, True),      # two hours of 187 minutes: 64%
    (11100, 11220, False),    # ends two minutes early: a quiet outro
    (3000, 3600, False),      # 83%: inside a fifth
    (120, 700, False),        # short clip: under ten minutes missing
])
def test_shortfall(last, seconds, short):
    segs = [] if last is None else [{"t": 0, "text": "a"},
                                     {"t": last, "text": "b"}]
    assert bool(ingest_mcg.shortfall(segs, seconds)) is short


def test_redo_removes_old_passages_and_the_shelf_row(monkeypatch, tmp_path):
    shelf = tmp_path / "shelf.json"
    shelf.write_text('[{"id": "a"}, {"id": "b"}]')
    monkeypatch.setattr(ingest_mcg, "SHELF", shelf)
    monkeypatch.setattr(ingest_mcg, "ARCHIVE", "threadguy")
    deleted, asked = [], {}

    class Index:
        def query(self, **k):
            asked.update(k)
            return {"matches": [{"id": f"b-{n}"} for n in range(150)]}

        def delete(self, ids, namespace):
            deleted.append((len(ids), namespace))

    class Fake:
        def __init__(self, namespace, index_name):
            self.index = Index()
    monkeypatch.setattr(ingest_mcg, "PodcastIndex", Fake)

    assert ingest_mcg.forget("b") == 150
    assert asked["filter"] == {"episode_id": {"$eq": "b"}}
    assert asked["namespace"] == "threadguy"
    assert deleted == [(100, "threadguy"), (50, "threadguy")]
    assert [r["id"] for r in ingest_mcg.shelf()] == ["a"]


# --- a redo keeps the old copy until the new one is in --------------------
#
# 82 ThreadGuy streams have 15-minute holes, from Groq chunks lost before
# the 1 Oct fix. Redoing them used to start by deleting each one, so any
# failure on the way (a download, a refused chunk, an embed) left a stream
# with nothing searchable. These pin the new order.

ITEM = {"id": "vid", "title": "Stream", "seconds": 3600, "format": "stream"}


def _segs(n: int) -> list[dict]:
    return [{"t": float(60 * i), "text": f"line {i}"} for i in range(n)]


class _FakeIndex:
    """ingest and rows_for, with every call written down in order."""

    def __init__(self, events, ids, fail=False):
        self.events, self.ids, self.fail = events, ids, fail

    async def ingest(self, episodes):
        self.events.append("ingest")
        if self.fail:
            raise ConnectionError("voyage blip")
        return len(self.ids)

    def rows_for(self, ep):
        return [{"id": i, "title": ep.title, "text": f"t{n}",
                 "text_ts": f"[0:0{n}:00] line {n}"}
                for n, i in enumerate(self.ids)]


@pytest.fixture
def redo(monkeypatch, tmp_path):
    from app import local_cache
    for name in ("HITS", "WINDOWS", "LINES"):
        monkeypatch.setattr(local_cache, name, tmp_path / name.lower())
    shelf = tmp_path / "shelf.json"
    shelf.write_text(json.dumps([dict(ITEM, url="u", published_at="2026-05-15")]))
    monkeypatch.setattr(ingest_mcg, "SHELF", shelf)
    monkeypatch.setattr(ingest_mcg, "ARCHIVE", "threadguy")
    events: list = []
    monkeypatch.setattr(ingest_mcg, "passage_ids",
                        lambda vid: events.append("list") or ["old1", "kept"])
    monkeypatch.setattr(ingest_mcg, "drop_passages",
                        lambda ids: events.append(("drop", list(ids))))
    return events, local_cache


def test_a_redo_deletes_only_after_the_new_passages_are_in(redo):
    events, cache = redo
    cache.put_lines("threadguy", "vid", _segs(3))
    index = _FakeIndex(events, ["kept", "new1", "new2"])
    note = asyncio.run(ingest_mcg.shelve(index, ITEM, _segs(5), "2026-05-15", replace=True))
    assert events == ["list", "ingest", ("drop", ["old1"])], \
        "listed, then written, then only the passage the new copy did not overwrite"
    assert "1 old ones removed" in note
    assert [r["id"] for r in ingest_mcg.shelf()] == ["vid"], "the row is never taken off"
    assert [ln["t"] for ln in cache.load_lines("threadguy", "vid")] == [0, 60, 120]
    assert [w["id"] for w in cache.load_windows("threadguy", "vid")] == ["kept", "new1", "new2"]


def test_a_failed_embed_on_a_redo_deletes_nothing(redo):
    events, cache = redo
    cache.put_lines("threadguy", "vid", _segs(3))
    with pytest.raises(ConnectionError):
        asyncio.run(ingest_mcg.shelve(_FakeIndex(events, ["n"], fail=True),
                                      ITEM, _segs(5), "", replace=True))
    assert not [e for e in events if e[0] == "drop"]
    assert len(cache.load_lines("threadguy", "vid")) == 3, "the old lines are still here"
    assert [r["id"] for r in ingest_mcg.shelf()] == ["vid"]


def test_a_shorter_transcript_is_not_written_over_a_longer_one(redo):
    events, cache = redo
    cache.put_lines("threadguy", "vid", _segs(5))
    with pytest.raises(RuntimeError, match="kept the old one"):
        asyncio.run(ingest_mcg.shelve(_FakeIndex(events, ["n"]), ITEM, _segs(4), "",
                                      replace=True))
    assert events == [], "refused before the index was touched"


def test_a_new_episode_lists_nothing_and_keeps_its_timed_lines(redo):
    events, cache = redo
    asyncio.run(ingest_mcg.shelve(_FakeIndex(events, ["a", "b"]), ITEM, _segs(2), ""))
    assert events == ["ingest"]
    assert cache.load_lines("threadguy", "vid") == [{"t": 0, "text": "line 0"},
                                                    {"t": 60, "text": "line 1"}]


def test_an_episode_too_big_to_list_is_not_redone(monkeypatch):
    """A query names at most a thousand; deleting only those would leave
    the rest of the broken copy beside the new one."""
    class Index:
        def query(self, **k):
            return {"matches": [{"id": str(n)} for n in range(ingest_mcg.MAX_PASSAGES)]}
    monkeypatch.setattr(ingest_mcg, "_raw_index", lambda: (Index(), "threadguy", 4))
    with pytest.raises(RuntimeError, match="not redone"):
        ingest_mcg.passage_ids("big")


def test_main_no_longer_deletes_before_transcribing():
    """The old main() called forget() on every --redo id before any audio
    was fetched. Read the source: that call must not come back."""
    import inspect
    src = inspect.getsource(ingest_mcg.main)
    assert "forget(" not in src
    assert "shelve(" in src


def test_the_groq_key_comes_from_settings_when_the_env_has_none(monkeypatch):
    """launchd starts the 09:00 job with an empty environment."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    class S:
        groq_api_key = " gsk_test \n"
    monkeypatch.setattr(ingest_mcg, "get_settings", lambda: S())
    assert ingest_mcg.groq_key() == "gsk_test"
    monkeypatch.setenv("GROQ_API_KEY", "from_env")
    assert ingest_mcg.groq_key() == "from_env"


# --- the day it went out, which on a daily show is the point -------------

@pytest.mark.parametrize("printed, day", [
    ("20260930 20261001\n", "2026-09-30"),   # a stream: aired, then processed
    ("NA 20260929\n", "2026-09-29"),          # an upload: no release date
    ("20260929 20260929\n", "2026-09-29"),
    ("NA NA\n", ""),
    ("", ""),
])
def test_the_air_date_wins_over_the_processing_date(printed, day):
    assert ingest_mcg.air_date(printed) == day


def test_the_lookup_asks_for_both_dates(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "20260930 20261001\n", "")
    monkeypatch.setattr(ingest_mcg.subprocess, "run", fake_run)
    assert ingest_mcg.published("bX6sTpFxkJs") == "2026-09-30"
    assert "%(release_date)s %(upload_date)s" in seen[0]


def test_a_video_already_indexed_from_elsewhere_is_not_pending(monkeypatch, tmp_path):
    """A shelf row from X that names its YouTube twin keeps the twin out of
    the queue; everything else new and long enough is still picked up."""
    shelf = tmp_path / "shelf.json"
    shelf.write_text(json.dumps([
        {"id": "x-1", "source": "x", "youtube": "AgeGated001"},
        {"id": "OnShelf0001"}]))
    monkeypatch.setattr(ingest_mcg, "SHELF", shelf)
    monkeypatch.setattr(ingest_mcg, "TABS", [("stream", "streams")])
    monkeypatch.setattr(ingest_mcg, "enumerate_tab", lambda url, limit: [
        {"id": "AgeGated001", "duration": 7000},
        {"id": "OnShelf0001", "duration": 7000},
        {"id": "BrandNew001", "duration": 7000},
        {"id": "AShortClip1", "duration": 60}])
    assert [t["id"] for t in ingest_mcg.pending(20)] == ["BrandNew001"]



@pytest.mark.parametrize("printed, waiting", [
    ("post_live\n", True), ("was_live\n", False), ("not_live\n", False),
    ("", False), ("NA\n", False)])
def test_a_replay_youtube_is_still_processing_is_left_for_later(
        monkeypatch, printed, waiting):
    """22 Oct 2026: three streams in two days downloaded as their first
    few minutes while YouTube still called them post_live."""
    monkeypatch.setattr(ingest_mcg.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, printed, ""))
    assert ingest_mcg.still_processing("mhlj7R97I8w") is waiting


def test_the_check_runs_before_anything_is_downloaded():
    src = Path(ingest_mcg.__file__).read_text()
    body = src[src.index("def prepare(item: dict)"):]
    assert body.index("still_processing(") < body.index("fetch_audio(")

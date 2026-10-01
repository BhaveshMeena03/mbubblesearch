"""The MCG ingest, and the two ways its first scheduled run failed quietly.

Both are the same shape of bug: the job went green having done nothing,
which from outside is indistinguishable from an archive already up to date.
"""
import subprocess

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

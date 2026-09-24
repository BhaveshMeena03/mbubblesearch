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

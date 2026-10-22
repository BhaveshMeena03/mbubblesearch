"""The Market Bubble audio fetcher tries more than one YouTube client."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_audio  # noqa: E402


def test_a_refused_client_moves_on_to_the_next(monkeypatch, tmp_path):
    """22 Oct 2026: the default client got 403 on all 18 appearances."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "youtube:player_client=tv_embedded" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        (tmp_path / "vid.mp3").write_bytes(b"ok")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fetch_audio.subprocess, "run", fake_run)
    assert fetch_audio.download("https://www.youtube.com/watch?v=vid",
                                tmp_path / "vid")
    # Both fragment settings on the first client, then the next client.
    assert sum("youtube:player_client=tv_embedded" in c for c in calls) == 2
    assert not any("--extractor-args" in c for c in calls[2:])


def test_an_x_broadcast_is_fetched_as_before(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        (tmp_path / "x.mp3").write_bytes(b"ok")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fetch_audio.subprocess, "run", fake_run)
    assert fetch_audio.download("https://x.com/i/broadcasts/1abc", tmp_path / "x")
    assert len(calls) == 1 and "--extractor-args" not in calls[0]

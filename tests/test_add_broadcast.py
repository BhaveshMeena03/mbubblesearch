"""add_broadcast.py: recordings with no talk in them are not indexed."""

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import episode_store  # noqa: E402
from scripts import add_broadcast as ab  # noqa: E402
from scripts import add_x_recordings as rec  # noqa: E402


def lyrics(minutes: int = 45) -> dict:
    """Like Ansem's 10 Nov chart video: a DJ set's lines and "Thank you" over 45 minutes."""
    lines = ["Deep, deep, where the sun don't shine", "Thank you.", "Is a place that I call home"]
    return {"episode_id": "x-2120009419820007926", "title": "Ansem on X: SOL, BTC and ZEC",
            "segments": [{"t": minutes * 60 * n / 200, "text": lines[n % 3]} for n in range(200)]}


def talk(minutes: int = 30, episode_id: str = "x-2116220650058846455") -> dict:
    """About 150 words a minute, a person talking."""
    words = " ".join(["so the level i'm watching on sol is the weekly close"] * 2)
    return {"episode_id": episode_id, "title": "Ansem on X: market thoughts",
            "segments": [{"t": 8.0 * n, "text": words} for n in range(minutes * 60 // 8)]}


def test_music_is_far_under_the_bar_and_talk_is_far_over():
    assert ab.words_per_minute(lyrics()) < 25
    assert ab.words_per_minute(talk()) > 120
    assert ab.words_per_minute({"segments": []}) == 0


def test_a_quiet_recording_is_taken_back_out_and_the_rest_kept(tmp_path):
    path = tmp_path / "episodes.json"
    other = talk(episode_id="x-1")
    episode_store.merge([other, lyrics(), talk()], path)
    kept = ab.drop_quiet([lyrics(), talk()], path)
    assert [e["episode_id"] for e in kept] == ["x-2116220650058846455"]
    assert {e["episode_id"] for e in episode_store.load(path)} == {"x-1", "x-2116220650058846455"}


def test_nothing_quiet_leaves_the_store_untouched(tmp_path):
    path = tmp_path / "episodes.json"
    episode_store.merge([talk()], path)
    before = path.read_bytes()
    assert ab.drop_quiet([talk()], path) == [talk()]
    assert path.read_bytes() == before


def test_the_unattended_job_reads_the_same_exit_code():
    assert rec.NO_TALK == ab.NO_TALK != 0


def test_every_shipped_episode_has_talk_in_it():
    """Two silent chart videos with house music went in on 9 and 10 Nov 2026."""
    shipped = json.loads(gzip.decompress((ROOT / "data" / "episodes.json.gz").read_bytes()))
    quiet = [(e["episode_id"], round(ab.words_per_minute(e))) for e in shipped
             if e.get("segments") and ab.words_per_minute(e) < ab.MIN_WORDS_PER_MINUTE]
    assert quiet == []

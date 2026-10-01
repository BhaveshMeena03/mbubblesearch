"""ThreadGuy's episode notes: written by a script, served by one route,
drawn under whichever player is showing the episode.

The notes promise a second for every topic, so most of what is checked
here is that a time the episode does not have never reaches the page,
and that nothing a model wrote goes into the page as markup.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main as main_module  # noqa: E402
from scripts import summarize_threadguy as summ  # noqa: E402

PAGE = (ROOT / "demo" / "threadguy.html").read_text()
STARTS = [0.0, 30.0, 95.0, 400.0, 401.0, 900.0, 1500.0, 2400.0]


def reply(topics, tldr="ThreadGuy covers the market open and a long guest "
                       "segment about Zcash."):
    return "here you go\n" + json.dumps({"tldr": tldr, "topics": topics})


def test_topics_snap_to_the_line_they_start_on():
    got = summ.parse(reply([{"t": 97, "text": "a"}, {"t": 905, "text": "b"},
                            {"t": 1600, "text": "c"}, {"t": 2401, "text": "d"}]),
                     2500, STARTS)
    assert [t["t"] for t in got["topics"]] == [95, 900, 1500, 2400]


def test_a_time_past_the_end_is_dropped_not_kept():
    got = summ.parse(reply([{"t": 30, "text": "a"}, {"t": 400, "text": "b"},
                            {"t": 900, "text": "c"}, {"t": 1500, "text": "d"},
                            {"t": 99999, "text": "nowhere"}]), 2500, STARTS)
    assert all(t["t"] <= 2500 for t in got["topics"])
    assert "nowhere" not in json.dumps(got)


def test_two_topics_on_the_same_stretch_are_one():
    got = summ.parse(reply([{"t": 400, "text": "a"}, {"t": 401, "text": "a again"},
                            {"t": 900, "text": "b"}, {"t": 1500, "text": "c"},
                            {"t": 2400, "text": "d"}]), 2500, STARTS)
    assert [t["t"] for t in got["topics"]] == [400, 900, 1500, 2400]


def test_too_few_topics_is_refused():
    with pytest.raises(ValueError, match="usable topics"):
        summ.parse(reply([{"t": 400, "text": "a"}, {"t": 401, "text": "b"}]),
                   2500, STARTS)


def test_no_dashes_reach_the_page():
    got = summ.parse(reply([{"t": 30, "text": "Zcash — up"},
                            {"t": 400, "text": "b"}, {"t": 900, "text": "c"},
                            {"t": 1500, "text": "d"}],
                           tldr="A stream – with a guest who talks about Zcash a lot."),
                     2500, STARTS)
    assert "—" not in json.dumps(got, ensure_ascii=False)
    assert "–" not in json.dumps(got, ensure_ascii=False)


def test_an_incomplete_transcript_is_not_summarized():
    """Six minutes of a three hour stream, summarized, reads as the stream."""
    class Never:
        @property
        def messages(self):
            raise AssertionError("no model call for a broken transcript")
    segs = [{"t": 0.0, "text": "gm"}, {"t": 360.0, "text": "ok"}]
    with pytest.raises(ValueError, match="incomplete transcript"):
        asyncio.run(summ.summarize(Never(), "m", {"seconds": 11220, "title": "x"}, segs))


@pytest.fixture
def client(monkeypatch, tmp_path):
    notes = {"abc": {"tldr": "t", "topics": [{"t": 5, "text": "x"}],
                     "model": "m", "title": "T", "published_at": "2026-09-30"}}
    path = tmp_path / "s.json.gz"
    path.write_bytes(gzip.compress(json.dumps(notes).encode()))
    monkeypatch.setattr(main_module, "THREADGUY_SUMMARIES", path)
    monkeypatch.setattr(main_module, "_THREADGUY_SUMMARIES_CACHE", None)
    with TestClient(main_module.app) as c:
        yield c
    main_module._THREADGUY_SUMMARIES_CACHE = None


def test_the_route_serves_only_what_the_page_draws(client):
    body = client.get("/v1/threadguy/summaries").json()
    assert body["count"] == 1
    assert body["summaries"]["abc"] == {"tldr": "t", "topics": [{"t": 5, "text": "x"}]}


def test_a_missing_file_is_no_notes_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "THREADGUY_SUMMARIES", tmp_path / "none.gz")
    monkeypatch.setattr(main_module, "_THREADGUY_SUMMARIES_CACHE", None)
    assert main_module._threadguy_summaries() == {}
    main_module._THREADGUY_SUMMARIES_CACHE = None


def test_the_page_keys_notes_by_the_field_the_api_sends():
    """The episodes route sends episode_id. Keyed by e.id, every lookup
    missed and no row ever showed notes, without an error anywhere."""
    assert "NOTES[e.episode_id]" in PAGE
    assert "NOTES[e.id]" not in PAGE


def test_model_text_goes_in_as_text():
    block = PAGE[PAGE.index("function notes(e, frame)"):PAGE.index("function row(e)")]
    assert "innerHTML" not in block
    assert "textContent = sm.tldr" in block and "textContent = t.text" in block


def test_notes_failing_to_load_does_not_stop_the_shelf():
    assert 'fetch("/v1/threadguy/summaries")' in PAGE
    i = PAGE.index('fetch("/v1/threadguy/summaries")')
    assert ".catch(function(){})" in PAGE[i:i + 300]


def test_a_topic_moves_the_player_it_sits_under():
    """Chapters, not a second video: the time sets the src of the frame
    it was given, the shelf row's or the stage's."""
    assert 'notes(e, function(){ return pw.querySelector("iframe"); })' in PAGE
    assert 'notes(e, function(){ return st.querySelector("iframe"); })' in PAGE

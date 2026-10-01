"""Clips from the ThreadGuy archive.

MCG and Market Bubble could cut a clip; ThreadGuy could not. It keeps no
transcripts on disk either, so the captions are rebuilt from the episode's
vectors exactly as MCG's are, and the clip goes through the same queue.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from tests.test_mcg_clip import FakeIndexHolder, FakeService

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "demo" / "threadguy.html").read_text()


@pytest.fixture
def episode_id():
    rows = main_module._threadguy_episodes()
    if not rows:
        pytest.skip("no ThreadGuy shelf in this checkout")
    return rows[0]["id"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_module, "ffmpeg_available", lambda: True)
    seen = []

    def rebuild(index, namespace, dimension, video_id):
        seen.append(namespace)
        return [{"t": 550.0, "text": "a line"}, {"t": 563.0, "text": "the bucket shop"}]
    monkeypatch.setattr(main_module.mcg_transcript, "rebuild", rebuild)
    main_module._mcg_clip_episodes.clear()
    main_module.clip_rate_limit._buckets.clear()
    with TestClient(main_module.app) as c:
        c.app.state.clips = FakeService()
        c.app.state.threadguy = FakeIndexHolder()
        c.namespaces = seen
        yield c
    main_module.clip_rate_limit._buckets.clear()
    main_module._mcg_clip_episodes.clear()


def test_a_clip_is_queued_with_captions_from_the_threadguy_vectors(client, episode_id):
    body = client.post("/v1/threadguy/clip", json={
        "episode_id": episode_id, "start": 548, "end": 578}).json()
    assert body["status"] == "queued" and body["seconds"] == 30.0
    episode, start, end = client.app.state.clips.submitted[0]
    assert (start, end) == (548.0, 578.0)
    assert episode["url"] and episode["segments"]
    assert client.namespaces == ["threadguy"], "read from its own namespace"


def test_an_unknown_episode_is_a_404(client):
    r = client.post("/v1/threadguy/clip", json={"episode_id": "nope", "start": 0, "end": 30})
    assert r.status_code == 404


def test_the_same_limits_as_every_other_archive(client, episode_id):
    r = client.post("/v1/threadguy/clip", json={
        "episode_id": episode_id, "start": 0, "end": 600})
    assert r.status_code == 400 and "between" in r.json()["detail"]


def test_without_a_renderer_the_route_is_honest(client, episode_id, monkeypatch):
    monkeypatch.setattr(main_module, "ffmpeg_available", lambda: False)
    r = client.post("/v1/threadguy/clip", json={
        "episode_id": episode_id, "start": 0, "end": 30})
    assert r.status_code == 503


def test_the_status_points_back_at_this_archive(client):
    client.app.state.clips.job.status = "done"
    assert client.get("/v1/threadguy/clip/job123").json()["url"] == \
        "/v1/threadguy/clip/job123/file"


def test_an_mcg_and_a_threadguy_episode_are_cached_apart(client, episode_id):
    """The cache is keyed by archive too: the same id cannot cross over."""
    client.post("/v1/threadguy/clip", json={"episode_id": episode_id, "start": 0, "end": 30})
    assert ("threadguy", episode_id) in main_module._mcg_clip_episodes


def test_the_download_name_cannot_be_set_by_the_caller():
    """The archive's name is fixed by the route. A parameter on a public
    route would have let anyone choose the file name a clip downloads as."""
    import inspect
    for route in (main_module.podcast_clip_file, main_module.mcg_clip_file,
                  main_module.threadguy_clip_file):
        assert set(inspect.signature(route).parameters) == {"job_id", "request"}


def test_the_page_offers_a_clip_on_every_passage():
    assert '<button type="button" class="clipbtn">make clip</button>' in PAGE
    assert 'fetch("/v1/threadguy/clip"' in PAGE
    # The finished clip is built as elements, not markup, from the url the
    # server returned.
    picker = PAGE.split("function clipPicker(h, card, btn){")[1].split("\n  function showHits")[0]
    assert 'document.createElement("video")' in picker
    assert "out.innerHTML = '<video" not in picker

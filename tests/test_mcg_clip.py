"""Clipping an archive that keeps no transcripts.

Market Bubble clips read their captions out of data/episodes.json. MCG has
no transcripts anywhere on disk on purpose, so the words come back from the
vectors instead -- which means this route has a failure the other one
cannot have: the index answering with nothing. That has to be a 404 about a
missing episode, never a 500 and never a clip with no captions.
"""
import pytest
from fastapi.testclient import TestClient

from app import main as main_module


class FakeJob:
    def __init__(self):
        self.id = "job123"
        self.status = "queued"
        self.error = None
        self.path = None


class FakeService:
    """The real renderer's shape, minus ffmpeg."""

    def __init__(self, depth=0):
        self.submitted = []
        self._depth = depth
        self.job = FakeJob()

    def queued_count(self):
        return self._depth

    def submit(self, episode, start, end):
        self.submitted.append((episode, start, end))
        return self.job

    def get(self, job_id):
        return self.job if job_id == self.job.id else None


class FakeIndexHolder:
    """Stands in for PodcastIndex: all the route wants is `.index`."""

    index = object()


@pytest.fixture
def episode_id():
    """A real MCG episode id, so the shelf lookup is genuine."""
    rows = main_module._mcg_episodes()
    assert rows, "no MCG shelf to test against"
    return rows[0]["id"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_module, "ffmpeg_available", lambda: True)
    # The transcript comes back without touching Pinecone.
    monkeypatch.setattr(main_module.mcg_transcript, "rebuild",
                        lambda *a, **kw: [{"t": 10.0, "text": "a line"},
                                          {"t": 14.0, "text": "another"}])
    main_module._mcg_clip_episodes.clear()
    # The limiter stays in the path; only its bucket is emptied. rpm=3 is
    # right for a renderer that costs a minute of CPU and wrong for a file
    # that queues a dozen fake ones -- without this the fourth request in
    # the file gets a 429, and every assertion after it is about rate
    # limiting rather than about clipping.
    main_module.clip_rate_limit._buckets.clear()
    with TestClient(main_module.app) as c:
        c.app.state.clips = FakeService()
        # Needs the `.index` attribute, not just presence: the route reads
        # the Pinecone handle off it, and a bare object() raises
        # AttributeError into the broad except that turns any lookup
        # failure into a 404 -- so the double failed the same way a real
        # outage would, and quietly.
        c.app.state.mcg = FakeIndexHolder()
        yield c
    main_module.clip_rate_limit._buckets.clear()
    main_module._mcg_clip_episodes.clear()


def test_a_clip_is_queued_with_the_episode_behind_it(client, episode_id):
    body = client.post("/v1/mcg/clip", json={
        "episode_id": episode_id, "start": 10, "end": 40}).json()
    assert body["status"] == "queued"
    assert body["seconds"] == 30.0

    episode, start, end = client.app.state.clips.submitted[0]
    assert (start, end) == (10.0, 40.0)
    assert episode["url"], "the renderer cannot fetch without a url"
    assert episode["segments"], "a clip with no captions is a broken clip"


def test_an_unknown_episode_is_a_404(client):
    r = client.post("/v1/mcg/clip", json={
        "episode_id": "not-an-episode", "start": 0, "end": 30})
    assert r.status_code == 404


def test_an_episode_the_index_cannot_rebuild_is_a_404(client, episode_id,
                                                      monkeypatch):
    """The failure the other archive cannot have."""
    monkeypatch.setattr(main_module.mcg_transcript, "rebuild",
                        lambda *a, **kw: [])
    main_module._mcg_clip_episodes.clear()
    r = client.post("/v1/mcg/clip", json={
        "episode_id": episode_id, "start": 0, "end": 30})
    assert r.status_code == 404


def test_an_index_that_raises_does_not_500(client, episode_id, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("pinecone is having a day")
    monkeypatch.setattr(main_module.mcg_transcript, "rebuild", boom)
    main_module._mcg_clip_episodes.clear()
    r = client.post("/v1/mcg/clip", json={
        "episode_id": episode_id, "start": 0, "end": 30})
    assert r.status_code == 404


@pytest.mark.parametrize("start,end", [(0, 1), (0, 600)])
def test_a_clip_outside_the_length_bounds_is_refused(client, episode_id,
                                                     start, end):
    r = client.post("/v1/mcg/clip", json={
        "episode_id": episode_id, "start": start, "end": end})
    assert r.status_code == 400


def test_a_full_queue_says_so_rather_than_hiding_it(client, episode_id):
    client.app.state.clips = FakeService(depth=4)
    r = client.post("/v1/mcg/clip", json={
        "episode_id": episode_id, "start": 0, "end": 30})
    assert r.status_code == 429


def test_without_a_renderer_the_route_is_honest(client, episode_id, monkeypatch):
    monkeypatch.setattr(main_module, "ffmpeg_available", lambda: False)
    r = client.post("/v1/mcg/clip", json={
        "episode_id": episode_id, "start": 0, "end": 30})
    assert r.status_code == 503


def test_the_status_points_back_at_this_archive(client):
    """A caller polling /v1/mcg should never be handed a /v1/podcast url."""
    client.app.state.clips.job.status = "done"
    body = client.get("/v1/mcg/clip/job123").json()
    assert body["ready"] is True
    assert body["url"] == "/v1/mcg/clip/job123/file"


def test_an_unfinished_clip_offers_no_url(client):
    body = client.get("/v1/mcg/clip/job123").json()
    assert body["status"] == "queued"
    assert body["url"] is None


def test_an_unknown_job_is_a_404(client):
    assert client.get("/v1/mcg/clip/nope").status_code == 404


def test_the_episode_is_rebuilt_once_and_then_reused(client, episode_id,
                                                     monkeypatch):
    """One Pinecone query per episode, not one per clip."""
    calls = {"n": 0}

    def counted(*a, **kw):
        calls["n"] += 1
        return [{"t": 1.0, "text": "a line"}]
    monkeypatch.setattr(main_module.mcg_transcript, "rebuild", counted)
    main_module._mcg_clip_episodes.clear()

    for _ in range(3):
        client.post("/v1/mcg/clip", json={
            "episode_id": episode_id, "start": 0, "end": 30})
    assert calls["n"] == 1

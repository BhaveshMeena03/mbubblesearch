"""Streamed answers for the front door and the ThreadGuy archive.

Both pages waited for the whole answer before showing a word, about five
seconds of "searching", while MCG, Elon and Market Bubble streamed. The
front door also searched its winning archive twice: once to score it and
again inside search(). The stream answers from the passages it scored.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.schemas import PodcastHit

HIT = PodcastHit(episode_id="ep1", title="Papertrade Launch", start_seconds=553,
                 timestamp="9:13", deep_link="https://www.youtube.com/watch?v=2tMMfjBqgvo&t=553s",
                 text="the bucket shop", score=0.7)


class StubIndex:
    retrieves = 0
    streams = 0
    mode = "ok"

    def __init__(self, *a, **k):
        pass

    async def retrieve(self, query, top_k=None):
        type(self).retrieves += 1
        return [] if self.mode == "empty" else [HIT]

    async def search(self, *a, **k):
        raise AssertionError("a stream must not call search(): it re-retrieves")

    async def answer_stream(self, query, hits):
        type(self).streams += 1
        if self.mode == "refusal":
            yield "\x00REFUSAL\x00"
            return
        for token in ("He called it", " a bucket shop at [9:22]."):
            yield token

    async def list_all(self):
        return []


class _Stub:
    def __init__(self, *a, **k):
        pass

    async def search(self, *a, **k):
        return []

    async def list_all(self):
        return []


@pytest.fixture
def client(monkeypatch):
    for name in ("Retriever", "ConciergeAgent", "IngestionPipeline", "SummaryStore"):
        monkeypatch.setattr(main_module, name, _Stub)
    monkeypatch.setattr(main_module, "PodcastIndex", StubIndex)
    StubIndex.retrieves = StubIndex.streams = 0
    StubIndex.mode = "ok"
    with TestClient(main_module.app) as c:
        yield c


def frames(text):
    out = []
    for chunk in (f for f in text.split("\n\n") if f.strip()):
        event = "message"
        data = ""
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        out.append((event, json.loads(data) if data else None))
    return out


def rooms_loaded(client):
    return [k for k, _, _ in main_module._ROOMS
            if getattr(client.app.state, k, None) is not None]


def test_threadguy_streams_passages_then_the_answer(client):
    if client.app.state.threadguy is None:
        pytest.skip("no ThreadGuy shelf in this checkout")
    r = client.post("/v1/threadguy/search/stream", json={"query": "bucket shops"})
    assert r.status_code == 200
    got = frames(r.text)
    assert got[0][0] == "hits"
    assert got[0][1][0]["timestamp"] == "9:13"
    assert {"url", "episode_seconds", "end_seconds"} <= set(got[0][1][0])
    assert "".join(d["text"] for e, d in got if e == "message") == \
        "He called it a bucket shop at [9:22]."
    assert got[-1][0] == "done"


def test_the_front_door_streams_scores_then_passages_then_the_answer(client):
    r = client.post("/v1/search/stream", json={"query": "bucket shops"})
    assert r.status_code == 200
    got = frames(r.text)
    assert [e for e, _ in got[:2]] == ["rooms", "hits"]
    rooms = got[0][1]
    assert rooms["archive"]["key"] in rooms_loaded(client)
    assert {c["key"] for c in rooms["considered"]} == set(rooms_loaded(client))
    assert "".join(d["text"] for e, d in got if e == "message").endswith("[9:22].")
    assert got[-1][0] == "done"


def test_the_winner_is_searched_once_not_twice(client):
    client.post("/v1/search/stream", json={"query": "searched once"})
    assert StubIndex.retrieves == len(rooms_loaded(client))
    assert StubIndex.streams == 1


def test_a_repeat_is_replayed_without_another_model_call(client):
    first = client.post("/v1/search/stream", json={"query": "asked twice"}).text
    calls = StubIndex.streams
    second = client.post("/v1/search/stream", json={"query": "asked twice"}).text
    assert StubIndex.streams == calls
    assert "".join(d["text"] for e, d in frames(second) if e == "message") == \
        "".join(d["text"] for e, d in frames(first) if e == "message")


def test_nothing_anywhere_says_so_without_calling_the_model(client):
    StubIndex.mode = "empty"
    got = frames(client.post("/v1/search/stream", json={"query": "nothing"}).text)
    assert got[0][0] == "rooms" and got[0][1]["archive"] is None
    assert got[-1][0] == "done"
    assert StubIndex.streams == 0


def test_a_refusal_is_its_own_event(client):
    StubIndex.mode = "refusal"
    text = client.post("/v1/search/stream", json={"query": "refuse"}).text
    assert "event: refusal" in text and "\x00" not in text

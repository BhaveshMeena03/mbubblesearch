"""Market Bubble's summaries are served from data/summaries.json.gz.

Reading every summary back from Pinecone was 0.6 MB a call, most of what
the site still read after the 19 Oct 2026 egress fix. The index now only
says which episodes have one; the text comes from the shipped file, and only
a summary the file lacks is fetched.
"""

import asyncio
import gzip
import json
import types

import pytest

from app import summaries
from app.schemas import Episode
from app.summaries import SummaryStore, load_shipped, save_shipped


class FakeIndex:
    def __init__(self, records, *, explode=False):
        self.records = records          # vector id -> metadata
        self.explode = explode
        self.fetched = []
        self.upserted = []

    def list(self, namespace):
        if self.explode:
            raise RuntimeError("429 egress limit")
        yield types.SimpleNamespace(
            vectors=[types.SimpleNamespace(id=i) for i in self.records])

    def fetch(self, ids, namespace):
        self.fetched += ids
        return types.SimpleNamespace(vectors={
            i: types.SimpleNamespace(metadata=self.records[i]) for i in ids})

    def upsert(self, vectors, namespace):
        self.upserted += vectors


def row(eid, published="2026-10-01", text="a summary " * 30):
    return {"episode_id": eid, "title": f"Show {eid}", "url": f"https://x/{eid}",
            "platform": "youtube", "summary": text, "published_at": published}


def store(index, path):
    s = SummaryStore.__new__(SummaryStore)
    s._settings = types.SimpleNamespace(pinecone_read_timeout_seconds=5,
                                        pinecone_write_timeout_seconds=5,
                                        embedding_dimension=4)
    s._index = index
    s._shipped_path = path
    s._shipped = None
    return s


def test_the_file_is_served_and_only_new_summaries_are_fetched(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a", "2026-10-01"), "b": row("b", "2026-10-08")}, path)
    index = FakeIndex({"summary-a": row("a", "2026-10-01"),
                       "summary-b": row("b", "2026-10-08"),
                       "summary-c": row("c", "2026-10-22")})
    rows = asyncio.run(store(index, path).list_all())
    assert [r["episode_id"] for r in rows] == ["c", "b", "a"]
    assert index.fetched == ["summary-c"]


def test_nothing_is_fetched_when_the_file_is_current(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    index = FakeIndex({"summary-a": row("a")})
    assert len(asyncio.run(store(index, path).list_all())) == 1
    assert index.fetched == []


def test_a_summary_no_longer_in_the_index_is_not_served(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a"), "gone": row("gone")}, path)
    rows = asyncio.run(store(FakeIndex({"summary-a": row("a")}), path).list_all())
    assert [r["episode_id"] for r in rows] == ["a"]


def test_the_file_is_served_when_the_index_cannot_be_reached(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a"), "b": row("b", "2026-10-08")}, path)
    rows = asyncio.run(store(FakeIndex({}, explode=True), path).list_all())
    assert [r["episode_id"] for r in rows] == ["b", "a"]


def test_without_a_file_it_fails_as_before(tmp_path):
    with pytest.raises(RuntimeError):
        asyncio.run(store(FakeIndex({}, explode=True), tmp_path / "none.json.gz").list_all())


def test_without_a_file_every_summary_is_fetched(tmp_path):
    index = FakeIndex({f"summary-{i}": row(str(i)) for i in range(150)})
    rows = asyncio.run(store(index, tmp_path / "none.json.gz").list_all())
    assert len(rows) == 150 and len(index.fetched) == 150


def test_storing_a_summary_writes_it_into_the_file(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a", text="old " * 80)}, path)
    s = store(FakeIndex({}), path)
    episode = Episode(episode_id="a", title="Show a", url="https://x/a",
                      platform="youtube", published_at="2026-10-01", segments=[])
    asyncio.run(s.store(episode, "new " * 80))
    assert load_shipped(path)["a"]["summary"].startswith("new ")


def test_callers_cannot_change_the_cached_rows(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    s = store(FakeIndex({"summary-a": row("a")}), path)
    asyncio.run(s.list_all())[0]["summary"] = "changed"
    assert asyncio.run(s.list_all())[0]["summary"] != "changed"


def test_the_file_is_the_same_bytes_for_the_same_rows(tmp_path):
    one, two = tmp_path / "1.json.gz", tmp_path / "2.json.gz"
    save_shipped({"a": row("a"), "b": row("b")}, one)
    save_shipped({"b": row("b"), "a": row("a")}, two)
    assert one.read_bytes() == two.read_bytes()
    assert json.loads(gzip.decompress(one.read_bytes()))[0]["episode_id"] == "b"


def test_the_shipped_file_is_in_the_image():
    root = summaries.SHIPPED.parent.parent
    assert "COPY data/summaries.json.gz ./data/summaries.json.gz" in (root / "Dockerfile").read_text()
    assert "!data/summaries.json.gz" in (root / ".dockerignore").read_text()


def test_a_store_built_without_a_file_never_touches_the_real_one():
    before = summaries.SHIPPED.read_bytes() if summaries.SHIPPED.exists() else None
    s = SummaryStore.__new__(SummaryStore)
    s._settings = types.SimpleNamespace(pinecone_read_timeout_seconds=5,
                                        pinecone_write_timeout_seconds=5,
                                        embedding_dimension=4)
    s._index = FakeIndex({"summary-a": row("a")})
    episode = Episode(episode_id="zz-test", title="t", url="u", segments=[])
    asyncio.run(s.store(episode, "a summary " * 40))
    assert len(asyncio.run(s.list_all())) == 1
    after = summaries.SHIPPED.read_bytes() if summaries.SHIPPED.exists() else None
    assert before == after

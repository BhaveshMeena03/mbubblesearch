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


# -- what could go wrong in production ------------------------------------

def test_a_corrupt_file_is_ignored_and_everything_is_fetched(tmp_path):
    path = tmp_path / "summaries.json.gz"
    path.write_bytes(b"not gzip at all")
    index = FakeIndex({"summary-a": row("a")})
    assert [r["episode_id"] for r in asyncio.run(store(index, path).list_all())] == ["a"]
    assert index.fetched == ["summary-a"]


def test_a_truncated_file_is_ignored(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    path.write_bytes(path.read_bytes()[:20])
    assert load_shipped(path) == {}


def test_rows_without_an_episode_id_are_skipped(tmp_path):
    path = tmp_path / "summaries.json.gz"
    path.write_bytes(gzip.compress(json.dumps([row("a"), {"title": "no id"}, "junk"]).encode()))
    assert list(load_shipped(path)) == ["a"]


def test_an_id_without_the_usual_prefix_is_fetched_not_dropped(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    index = FakeIndex({"summary-a": row("a"), "legacy-b": row("b", "2026-09-01")})
    rows = asyncio.run(store(index, path).list_all())
    assert sorted(r["episode_id"] for r in rows) == ["a", "b"]
    assert index.fetched == ["legacy-b"]


def test_an_id_pinecone_cannot_return_does_not_break_the_list(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)

    class Gappy(FakeIndex):
        def fetch(self, ids, namespace):
            self.fetched += ids
            return types.SimpleNamespace(vectors={})
    rows = asyncio.run(store(Gappy({"summary-a": row("a"), "summary-b": row("b")}), path).list_all())
    assert [r["episode_id"] for r in rows] == ["a"]


def test_a_missing_or_null_date_sorts_last_instead_of_crashing(tmp_path):
    path = tmp_path / "summaries.json.gz"
    nodate = {**row("n"), "published_at": None}
    save_shipped({"a": row("a", "2026-10-01"), "n": nodate}, path)
    index = FakeIndex({"summary-a": row("a"), "summary-n": nodate,
                       "summary-z": {**row("z"), "published_at": None}})
    rows = asyncio.run(store(index, path).list_all())
    assert rows[0]["episode_id"] == "a" and len(rows) == 3


def test_an_index_that_hangs_times_out_and_the_file_is_served(tmp_path):
    import time
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)

    class Slow(FakeIndex):
        def list(self, namespace):
            time.sleep(2)
            yield from super().list(namespace)
    s = store(Slow({"summary-a": row("a")}), path)
    s._settings.pinecone_read_timeout_seconds = 0.2
    assert [r["episode_id"] for r in asyncio.run(s.list_all())] == ["a"]


def test_an_unwritable_file_does_not_fail_the_store(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    path = folder / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    folder.chmod(0o500)
    try:
        s = store(FakeIndex({}), path)
        episode = Episode(episode_id="b", title="b", url="u", segments=[])
        asyncio.run(s.store(episode, "a summary " * 40))
        assert len(s._index.upserted) == 1
    finally:
        folder.chmod(0o700)


def test_two_lists_at_once_agree(tmp_path):
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    s = store(FakeIndex({"summary-a": row("a"), "summary-b": row("b", "2026-10-09")}), path)

    async def both():
        return await asyncio.gather(s.list_all(), s.list_all())
    one, two = asyncio.run(both())
    assert one == two and len(one) == 2


def test_the_shipped_file_matches_what_the_site_lists():
    """The committed file is real data: every row has the fields the
    episode list renders, and no duplicate ids."""
    rows = json.loads(gzip.decompress(summaries.SHIPPED.read_bytes()))
    ids = [r["episode_id"] for r in rows]
    assert len(ids) == len(set(ids)) and len(ids) >= 70
    for r in rows:
        assert r.get("title") and r.get("url") and len(r.get("summary") or "") >= 200, r["episode_id"]


def test_pack_refuses_to_write_an_empty_file(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(summaries.SHIPPED.parent.parent / "scripts"))
    import pack_summaries
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    monkeypatch.setattr(pack_summaries, "SHIPPED", path)
    monkeypatch.setattr(pack_summaries, "load_shipped", lambda: load_shipped(path))
    monkeypatch.setattr(pack_summaries, "save_shipped", lambda rows: save_shipped(rows, path))

    class Empty:
        _shipped = None

        async def list_all(self):
            return []
    monkeypatch.setattr(pack_summaries, "SummaryStore", Empty)
    monkeypatch.setattr(sys, "argv", ["pack_summaries.py"])
    assert asyncio.run(pack_summaries.main()) == 1
    assert list(load_shipped(path)) == ["a"]


def test_pack_adds_what_is_new(tmp_path, monkeypatch, capsys):
    import sys
    sys.path.insert(0, str(summaries.SHIPPED.parent.parent / "scripts"))
    import pack_summaries
    path = tmp_path / "summaries.json.gz"
    save_shipped({"a": row("a")}, path)
    monkeypatch.setattr(pack_summaries, "SHIPPED", path)
    monkeypatch.setattr(pack_summaries, "load_shipped", lambda: load_shipped(path))
    monkeypatch.setattr(pack_summaries, "save_shipped", lambda rows: save_shipped(rows, path))

    class Two:
        _shipped = None

        async def list_all(self):
            return [row("a"), row("b", "2026-10-22")]
    monkeypatch.setattr(pack_summaries, "SummaryStore", Two)
    monkeypatch.setattr(sys, "argv", ["pack_summaries.py"])
    assert asyncio.run(pack_summaries.main()) == 0
    assert sorted(load_shipped(path)) == ["a", "b"]
    assert "1 new" in capsys.readouterr().out

"""Nothing reads its own output back out of Pinecone.

On 19 Oct 2026 the project's 10 GB monthly read cap was spent in nineteen
days, and visitors' searches were 15 MB of it. The rest: the dashboards
reloading every stored hit at every boot and every half hour, and the
Sunday exact-word rebuild fetching every passage of two archives. These
hold the three fixes: a local copy of what the morning job writes, the
dashboards served from the committed report when it covers the store,
and a meter on every Index the app constructs.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app import egress, local_cache

ROOT = Path(__file__).resolve().parent.parent


# --- the local caches ----------------------------------------------------

@pytest.fixture
def caches(monkeypatch, tmp_path):
    monkeypatch.setattr(local_cache, "HITS", tmp_path / "hits")
    monkeypatch.setattr(local_cache, "WINDOWS", tmp_path / "windows")
    return tmp_path


def test_hits_and_windows_round_trip_and_forget_removes_both(caches):
    hits = [{"episode_id": "x-123", "symbol": "ZEC"}, {"episode_id": "x-123", "symbol": "SOL"}]
    local_cache.put_hits("mcg", "x-123", hits)
    local_cache.put_windows("mcg", "x-123", [{"id": "a" * 32, "title": "t", "text": "words"}])
    assert local_cache.load_hits("mcg") == {"x-123": hits}
    assert local_cache.has_windows("mcg", "x-123")
    assert local_cache.load_windows("mcg", "x-123")[0]["text"] == "words"
    local_cache.forget("mcg", "x-123")
    assert local_cache.load_hits("mcg") == {}
    assert not local_cache.has_windows("mcg", "x-123")
    assert local_cache.load_windows("mcg", "x-123") == []


def test_an_episode_id_cannot_leave_the_cache_folder(caches):
    path = local_cache.put_hits("mcg", "../../etc/passwd", [])
    assert path.parent == local_cache.HITS / "mcg"
    assert ".." not in path.name and "/" not in path.name


def test_the_caches_are_gitignored_and_the_reports_are_not():
    ignored = (ROOT / ".gitignore").read_text()
    assert "data/.hits_cache/" in ignored and "data/.windows_cache/" in ignored
    assert "data/mcg_assets.json" not in ignored


# --- the meter -----------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class FakeIndex:
    def query(self, **kwargs):
        return FakeResponse({"matches": [{"id": "a", "metadata": {"text": "x" * 1000}}] * 10})

    def fetch(self, **kwargs):
        # Ten records with vectors, about 40 KB: enough to show at two
        # decimal places of a megabyte, which is how the snapshot reports.
        return FakeResponse({"vectors": {f"v{i}": {"values": [0.1] * 1024} for i in range(10)}})

    def list(self, **kwargs):
        yield type("Page", (), {"vectors": [1, 2, 3]})()


def test_every_read_is_weighed_by_kind(monkeypatch):
    monkeypatch.setattr(egress, "_by_day", egress.defaultdict(int))
    monkeypatch.setattr(egress, "_by_kind", egress.defaultdict(int))
    monkeypatch.setattr(egress, "_calls", egress.defaultdict(int))
    index = egress.metered(FakeIndex(), "test")
    index.query(namespace="podcast", top_k=10)
    index.fetch(ids=["a"], namespace="assets")
    list(index.list(namespace="assets"))
    snap = egress.snapshot()
    assert snap["calls"] == {"test:query:podcast": 1, "test:fetch:assets": 1,
                             "test:list:assets": 1}
    assert snap["by_kind_mb"]["test:query:podcast"] > 0.009
    assert snap["by_kind_mb"]["test:fetch:assets"] > 0.005
    assert snap["since_start_mb"] == snap["today_mb"] > 0
    # Wrapping twice does not count twice.
    assert egress.metered(index) is index


def test_the_apps_indexes_are_all_metered():
    for name in ("podcast", "questions", "summaries", "assets_store", "ingest"):
        src = (ROOT / "app" / f"{name}.py").read_text()
        assert "metered(Pinecone(" in src, name


# --- the dashboard reads the store only when the report is behind -------

class Store:
    def __init__(self, ids, hits):
        self.ids, self.hits, self.read = ids, hits, 0

    async def episode_ids(self):
        return set(self.ids)

    async def all_hits(self):
        self.read += 1
        return list(self.hits)


def hit(symbol, episode_id):
    return {"symbol": symbol, "name": symbol, "asset_class": "crypto",
            "episode_id": episode_id, "episode_title": "t", "start_seconds": 1.0,
            "timestamp": "0:01", "deep_link": "https://www.youtube.com/watch?v=" + episode_id,
            "confidence": "high", "note": "n", "kind": "mention"}


def test_a_current_committed_report_is_served_without_reading_the_store(tmp_path):
    from app import main as main_module
    report = {"assets": [{"symbol": "ZEC"}], "total_hits": 1, "episodes_processed": 2,
              "episode_ids": ["ep1", "ep2"]}
    fallback = tmp_path / "r.json"
    fallback.write_text(json.dumps(report))
    store = Store(["ep1", "ep2"], [hit("ZEC", "ep1")])
    out = asyncio.run(main_module._build_report(store, fallback, "_t_cache", "mcg"))
    assert out["episode_ids"] == ["ep1", "ep2"] and out["assets"][0]["symbol"] == "ZEC"
    assert store.read == 0


def test_a_report_missing_an_episode_the_store_has_is_rebuilt_from_the_store(tmp_path):
    from app import main as main_module
    fallback = tmp_path / "r.json"
    fallback.write_text(json.dumps({"assets": [], "total_hits": 0, "episode_ids": ["ep1"]}))
    store = Store(["ep1", "ep2"], [hit("ZEC", "ep1"), hit("SOL", "ep2")])
    out = asyncio.run(main_module._build_report(store, fallback, "_t_cache2", "mcg"))
    assert store.read == 1
    assert {a["symbol"] for a in out["assets"]} == {"ZEC", "SOL"}


def test_a_store_without_episode_ids_is_read_as_before(tmp_path):
    """The fakes in the other dashboard tests, and any older store."""
    from app import main as main_module

    class Old:
        async def all_hits(self):
            return [hit("ZEC", "ep1")]

    fallback = tmp_path / "r.json"
    fallback.write_text(json.dumps({"assets": [{"symbol": "STALE"}], "episode_ids": ["ep1"]}))
    out = asyncio.run(main_module._build_report(Old(), fallback, "_t_cache3", "mcg"))
    assert out["assets"][0]["symbol"] == "ZEC"


def test_the_market_bubble_extractor_keeps_the_same_cache():
    """Its dashboard read the store at every boot like the other two."""
    src = (ROOT / "scripts" / "extract_assets.py").read_text()
    assert 'local_cache.put_hits("podcast"' in src
    assert 'local_cache.load_hits("podcast")' in src
    assert 'report["episode_ids"] = sorted(cached)' in src
    seed = (ROOT / "scripts" / "seed_local_caches.py").read_text()
    assert '"podcast": ("pinecone_index", "assets")' in seed


def test_episode_ids_are_read_off_record_ids_with_dashes_in_them():
    from app.assets_store import AssetStore
    store = AssetStore.__new__(AssetStore)
    store._all_ids = lambda: ["assets-QH2d-awq0No-0", "assets-QH2d-awq0No-1",
                              "assets-x-2057559650166559016-0", "assets-legacy"]

    class S:
        pinecone_read_timeout_seconds = 5
    store._settings = S()
    assert asyncio.run(store.episode_ids()) == {"QH2d-awq0No", "x-2057559650166559016", "legacy"}


# --- the term index reads the cache, and the index only for what is missing

def test_the_term_index_reads_cached_passages_and_nothing_else(caches, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_term_index as b

    from scripts import ingest_mcg
    monkeypatch.setattr(ingest_mcg, "use", lambda archive: ingest_mcg.ARCHIVES[archive])
    monkeypatch.setattr(ingest_mcg, "shelf", lambda: [{"id": "v1"}, {"id": "v2"}])
    for vid in ("v1", "v2"):
        local_cache.put_windows("rasmr", vid, [{"id": vid + "a", "title": "T", "text": "hello " + vid}])

    def no_index(*a, **k):
        raise AssertionError("the index was read although every episode was cached")
    monkeypatch.setattr("app.podcast.PodcastIndex.__init__", no_index)
    ids, docs = b.from_cache("rasmr")
    assert ids == ["v1a", "v2a"] and docs[1] == ("T", "hello v2")


def test_the_ids_in_the_cache_are_the_ids_in_the_index():
    """One function names a passage everywhere."""
    import hashlib

    from app.podcast import vector_id
    assert vector_id("abc", 12.0) == hashlib.sha256(b"abc:12.0").hexdigest()[:32]
    src = (ROOT / "scripts" / "build_term_index.py").read_text()
    assert "from_cache(" in src and 'LOCAL = {"podcast"' in src

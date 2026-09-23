"""The MCG asset surface, and the line between the two archives.

Two things are being guarded here. The first is that a transcript can be
rebuilt from the index at all, because MCG keeps no local transcripts and
every asset row depends on that reconstruction being faithful -- a dropped
or misordered line is a moment that links to the wrong second.

The second is separation. The archives share a code path and nearly share a
URL, and the failure that would follow from mixing them is not a crash: it
is an MCG row carrying Market Bubble timestamps, where every link lands in
a video that never discussed it.
"""
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.assets_store import AssetStore
from app.mcg_transcript import rebuild, seconds, to_segments

# --- rebuilding a transcript from window metadata -------------------------

@pytest.mark.parametrize("stamp,want", [
    ("0:00", 0),
    ("2:11", 131),
    ("59:59", 3599),
    ("1:00:44", 3644),
    ("3:41:46", 13306),
])
def test_stamps_parse(stamp, want):
    assert seconds(stamp) == want


def test_lines_come_back_in_order_across_unordered_windows():
    segs = to_segments(["[2:00] second\n[3:00] third", "[0:30] first"])
    assert [s["text"] for s in segs] == ["first", "second", "third"]
    assert [s["t"] for s in segs] == [30, 120, 180]


def test_a_line_repeated_at_a_window_boundary_is_kept_once():
    segs = to_segments(["[1:00] the overlap line", "[1:00] the overlap line"])
    assert len(segs) == 1


def test_two_different_lines_on_the_same_second_both_survive():
    """Dedupe is on the text as well as the timestamp, not the timestamp."""
    segs = to_segments(["[1:00] first thing said\n[1:00] a different thing"])
    assert len(segs) == 2


def test_unstamped_and_empty_lines_are_dropped():
    segs = to_segments(["not a stamped line", "[1:00] ", "[2:00] real"])
    assert [s["text"] for s in segs] == ["real"]


def test_rebuild_sorts_windows_before_reading_them():
    """The query returns matches by score; the transcript is by time."""
    class Index:
        def query(self, **kw):
            return {"matches": [
                {"metadata": {"start_seconds": 600, "text_ts": "[10:00] later"}},
                {"metadata": {"start_seconds": 0, "text_ts": "[0:10] earlier"}},
            ]}
    segs = rebuild(Index(), "mcg", 1024, "vid")
    assert [s["text"] for s in segs] == ["earlier", "later"]


def test_rebuild_asks_only_for_the_episode_it_was_given():
    seen = {}

    class Index:
        def query(self, **kw):
            seen.update(kw)
            return {"matches": []}
    rebuild(Index(), "mcg", 1024, "zJsQAfBROiQ")
    assert seen["filter"] == {"episode_id": {"$eq": "zJsQAfBROiQ"}}
    assert seen["namespace"] == "mcg"
    assert len(seen["vector"]) == 1024


# --- the two archives must not share storage ------------------------------

def test_the_stores_point_at_different_places():
    mb, mcg = AssetStore(), AssetStore(index_name="mcg-search",
                                       namespace="assets")
    assert mb._index_name != mcg._index_name, (
        "both archives writing assets into one index would merge their rows")


def test_the_default_store_is_unchanged():
    """Existing callers construct it with no arguments and must not move."""
    from app.config import get_settings
    assert AssetStore()._index_name == get_settings().pinecone_index
    assert AssetStore()._namespace == "assets"


# --- the routes -----------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    """Both stores empty, so both reports come from their committed file."""
    async def nothing_stored():
        return []

    class Empty:
        all_hits = staticmethod(nothing_stored)

    async def no_market(ticker, asset_class=None):
        return None

    monkeypatch.setattr(main_module, "_market_for", no_market)
    with TestClient(main_module.app) as c:
        c.app.state.assets = Empty()
        c.app.state.mcg_assets = Empty()
        # Both caches cleared: the app may have served either report
        # already, and a warm slot would hide the bug this file is about.
        for slot in ("_assets_cache", "_mcg_assets_cache"):
            if hasattr(c.app.state, slot):
                delattr(c.app.state, slot)
        yield c


def test_the_mcg_dashboard_answers(client):
    body = client.get("/v1/mcg/assets").json()
    assert body["assets"], "no rows from the committed fallback"
    assert body["total_hits"] > 0


def test_the_two_dashboards_do_not_serve_each_other(client):
    """The cache-slot bug, stated as a test.

    One slot for both reports would make whichever archive was asked for
    second receive the first one's rows.
    """
    mb = client.get("/v1/assets").json()
    mcg = client.get("/v1/mcg/assets").json()
    assert mb["assets"] != mcg["assets"]

    mcg_links = [m["deep_link"] for a in mcg["assets"] for m in a["moments"]]
    assert mcg_links, "an MCG row with no moment to cite"
    # MCG is entirely YouTube; Market Bubble's broadcasts are on X. A link
    # to x.com in this report means Market Bubble rows leaked into it.
    assert not any("x.com" in link for link in mcg_links)


def test_an_unknown_mcg_ticker_is_a_404_not_a_guess(client):
    assert client.get("/v1/mcg/assets/NOTATICKER").status_code == 404


def test_a_hostile_mcg_ticker_is_refused(client):
    for bad in ("../../etc/passwd", "'; DROP--", "<script>"):
        assert client.get(f"/v1/mcg/assets/{bad}").status_code in (404, 400)


def test_an_mcg_row_reports_its_own_archive(client):
    symbol = client.get("/v1/mcg/assets").json()["assets"][0]["symbol"]
    body = client.get(f"/v1/mcg/assets/{symbol}").json()
    assert body["symbol"] == symbol
    assert "MCG Live" in body["disclaimer"]
    assert body["moments"], "a detail view with nothing to cite"


def test_the_short_path_reaches_the_page(client):
    r = client.get("/mcg/assets", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/demo/mcg-assets.html"


# --- the second metadata shape -------------------------------------------
#
# Streams store `text` plus a comma-separated `line_times`; interviews store
# the stamped copy as `text_ts`. Reading only the latter skipped 230 of 645
# episodes -- 727 hours, every one a stream -- and looked exactly like an
# archive where nothing was said.

def test_a_stream_window_rebuilds_from_line_times():
    class Index:
        def query(self, **kw):
            return {"matches": [{"metadata": {
                "start_seconds": 1335,
                "text": "first line\nsecond line",
                "line_times": "1335,1342",
            }}]}
    segs = rebuild(Index(), "mcg", 1024, "vid")
    assert [s["text"] for s in segs] == ["first line", "second line"]
    assert [s["t"] for s in segs] == [1335, 1342]


def test_both_metadata_shapes_rebuild_together():
    """One episode can hold windows of each shape; neither may be dropped."""
    class Index:
        def query(self, **kw):
            return {"matches": [
                {"metadata": {"start_seconds": 0, "text_ts": "[0:05] stamped"}},
                {"metadata": {"start_seconds": 60, "text": "timed",
                              "line_times": "60"}},
            ]}
    segs = rebuild(Index(), "mcg", 1024, "vid")
    assert [s["text"] for s in segs] == ["stamped", "timed"]


def test_times_that_do_not_line_up_are_not_invented():
    """Fewer times than lines means the stamps are unknown, not guessable."""
    class Index:
        def query(self, **kw):
            return {"matches": [{"metadata": {
                "start_seconds": 10, "text": "a\nb\nc", "line_times": "10,20"}}]}
    assert rebuild(Index(), "mcg", 1024, "vid") == []

"""Usage history that survives a deploy (app/traffic.py)."""

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import traffic as tr
from app.main import TRAFFIC, app


class FakeIndex:
    def __init__(self, stored: dict | None = None, fail: bool = False):
        self.stored = stored or {}
        self.upserts = []
        self.fail = fail

    def fetch(self, ids, namespace):
        assert namespace == tr.NAMESPACE
        vectors = {i: SimpleNamespace(metadata=self.stored[i]) for i in ids
                   if i in self.stored}
        return SimpleNamespace(vectors=vectors)

    def upsert(self, vectors, namespace):
        if self.fail:
            raise RuntimeError("pinecone down")
        self.upserts.append(vectors)
        for v in vectors:
            self.stored[v["id"]] = v["metadata"]


def fresh(index=None) -> tr.Traffic:
    return tr.Traffic(index=index, salt="s", dimension=4)


def test_people_count_and_link_previews_do_not():
    t = fresh()
    t.page("/threadguy", "1.2.3.4", "Mozilla/5.0 (iPhone)", "https://t.co/abc")
    t.page("/threadguy", "1.2.3.4", "Mozilla/5.0 (iPhone)", None)
    t.page("/threadguy", "9.9.9.9", "Twitterbot/1.0", None)
    t.page("/v1/threadguy/search", "1.2.3.4", "Mozilla/5.0", None)   # an API call
    day = t.history()[0]
    assert day["page_views"] == 2
    assert day["visitors"] == 1, "same person twice is one visitor"
    assert day["bot_views"] == 1
    assert day["referrers"] == {"t.co": 1, "direct": 1}


def test_no_address_is_ever_stored():
    t = fresh()
    t.page("/home", "49.36.72.251", "Mozilla/5.0", None)
    meta = tr.to_metadata("2026-10-02", t._pending[tr.today()])
    assert "49.36.72.251" not in json.dumps(meta)


def test_the_same_person_is_a_different_hash_tomorrow():
    assert tr.visitor_hash("1.2.3.4", "2026-10-02", "s") != \
        tr.visitor_hash("1.2.3.4", "2026-10-03", "s")


def test_searches_add_up_across_archives():
    t = fresh()
    for kind in ("front_door_searches", "threadguy_searches", "threadguy_searches",
                 "episode_summary_views"):
        t.event(kind)
    assert t.history()[0]["searches"] == 3


def test_a_flush_adds_to_what_another_instance_already_stored():
    """During a deploy two instances run at once; neither may erase the other."""
    day = tr.today()
    earlier = tr.empty_day()
    earlier["counts"]["mcg_searches"] = 4
    earlier["visitors"] = {"aaaaaaaaaa"}
    index = FakeIndex({f"traffic-{day}": tr.to_metadata(day, earlier)})
    t = fresh(index)
    t.event("mcg_searches")
    t.page("/mcg", "5.5.5.5", "Mozilla/5.0", None)
    asyncio.run(t.flush())
    stored = tr.from_metadata(index.stored[f"traffic-{day}"])
    assert stored["counts"]["mcg_searches"] == 5
    assert len(stored["visitors"]) == 2
    assert t._pending == {}, "flushed, so not counted twice next time"


def test_a_failed_flush_keeps_the_counts_for_the_next_one():
    t = fresh(FakeIndex(fail=True))
    t.event("podcast_searches")
    asyncio.run(t.flush())
    assert t._pending[tr.today()]["counts"]["podcast_searches"] == 1


def test_history_starts_where_the_last_deploy_left_off():
    day = tr.today()
    stored = tr.empty_day()
    stored["counts"]["podcast_searches"] = 12
    t = fresh(FakeIndex({f"traffic-{day}": tr.to_metadata(day, stored)}))
    asyncio.run(t.load(days=2))
    t.event("podcast_searches")
    assert t.history()[0]["searches"] == 13


def test_a_busy_day_still_fits_in_one_record():
    big = tr.empty_day()
    big["visitors"] = {f"{i:010x}" for i in range(5_000)}
    merged = tr.merge(big, tr.empty_day())
    meta = tr.to_metadata("2026-10-02", merged)
    assert len(json.dumps(meta).encode()) < 40_000
    assert len(merged["visitors"]) + merged["visitor_overflow"] == 5_000


def test_totals_say_visitor_days_not_people():
    t = fresh()
    t.page("/home", "1.1.1.1", "Mozilla/5.0", None)
    assert "visitor_days" in t.totals() and "visitors" not in t.totals()


def test_stats_endpoint_carries_the_history_and_pages_are_counted():
    before = sum(r["page_views"] for r in TRAFFIC.history())
    with TestClient(app) as c:
        c.get("/home", headers={"user-agent": "Mozilla/5.0"})
        body = c.get("/v1/stats").json()
    assert "history" in body and "totals" in body
    assert sum(r["page_views"] for r in body["history"]) == before + 1

"""The metrics page (/metrics) and what it draws from (/v1/metrics)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app import traffic as tr

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "demo" / "metrics.html"
SIZES = {"recordings": 1582, "hours": 2324,
         "by": {"podcast": {"count": 40, "appearances": 16, "hours": 120}}}


def fresh() -> tr.Traffic:
    return tr.Traffic(index=None, salt="s", dimension=4)


@pytest.fixture
def client(monkeypatch):
    t = fresh()
    t._stored = {
        "2026-10-21": tr.merge(tr.empty_day(), tr.empty_day()),
        "2026-10-22": tr.merge(tr.empty_day(), tr.empty_day()),
    }
    t._stored["2026-10-22"]["visitors"] = {"a", "b", "c"}
    t._stored["2026-10-22"]["pages"].update({"/home": 5})
    t._stored["2026-10-22"]["referrers"].update({"t.co": 3, "x.com": 1, "google.com": 1})
    t._stored["2026-10-21"]["visitors"] = {"d"}
    t._stored["2026-10-21"]["counts"].update({"podcast_searches": 2, "mcg_searches": 1})

    async def sizes(request):
        return SIZES

    monkeypatch.setattr(main, "TRAFFIC", t)
    monkeypatch.setattr(main, "_archive_sizes", sizes)
    with TestClient(main.app) as c:
        yield c


def test_days_come_oldest_first_with_only_what_the_page_draws(client):
    days = client.get("/v1/metrics").json()["days"]
    assert [d["day"] for d in days] == ["2026-10-21", "2026-10-22"]
    assert set(days[0]) == {"day", "visitors", "page_views", "searches", "from_x"}


def test_no_address_hash_or_page_list_leaves_the_server(client):
    body = client.get("/v1/metrics").text
    for leak in ('"a"', "visitor_hashes", "top_pages", "referrers", "events"):
        assert leak not in body


def test_visits_from_x_add_up_t_co_and_x_but_not_search(client):
    days = {d["day"]: d for d in client.get("/v1/metrics").json()["days"]}
    assert days["2026-10-22"]["from_x"] == 4
    assert days["2026-10-21"]["searches"] == 3
    assert days["2026-10-22"]["visitors"] == 3


def test_totals_and_archives_ride_along(client):
    body = client.get("/v1/metrics").json()
    assert body["totals"]["visitor_days"] == 4
    assert body["archives"] == SIZES


def test_from_x_counts_referrers_past_the_six_that_are_kept():
    day = tr.empty_day()
    day["referrers"].update({f"site{i}.com": 50 for i in range(8)})
    day["referrers"].update({"t.co": 2, "mobile.twitter.com": 1})
    summary = tr.day_summary("2026-10-22", day)
    assert "t.co" not in summary["referrers"]
    assert summary["from_x"] == 3


def test_an_empty_history_is_an_empty_list_not_an_error(monkeypatch):
    async def sizes(request):
        return None

    monkeypatch.setattr(main, "TRAFFIC", fresh())
    monkeypatch.setattr(main, "_archive_sizes", sizes)
    with TestClient(main.app) as c:
        r = c.get("/v1/metrics")
    assert r.status_code == 200
    assert r.json()["days"] == [] and r.json()["archives"] is None


def test_the_page_has_a_short_address():
    with TestClient(main.app) as c:
        r = c.get("/metrics", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/demo/metrics.html"


def test_the_page_is_kept_out_of_search_and_reads_the_endpoint():
    html = PAGE.read_text()
    assert re.search(r'<meta name="robots" content="noindex', html)
    assert 'fetch("/v1/metrics")' in html


def test_the_page_says_what_it_does_not_count():
    html = PAGE.read_text()
    assert "visitor is counted once a day" in html
    assert "not in these numbers" in html


def test_the_page_has_no_long_dashes():
    assert "—" not in PAGE.read_text() and "–" not in PAGE.read_text()


def test_the_page_ships_in_the_image():
    assert "COPY demo ./demo" in (ROOT / "Dockerfile").read_text()

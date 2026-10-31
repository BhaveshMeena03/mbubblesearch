"""The browser extension in extension/ and the three routes it calls.

The extension puts a button on X posts that are recordings in these
archives: the summary, an ask box, and times that move X's own player to
the second. Built 31 Oct 2026. What has to stay true:

  - a post gets its archive's episode and the right clock, from files the
    service ships, and a broken file costs only its own archive
  - every time shown converts to X's clock here, the one copy of that sum
  - a question about a post is answered from that recording only, with
    every limit any other question has, and the caller picks nothing but
    the post
  - the extension asks for no permission it does not use, talks to one
    origin, and never parses markup
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import x_posts
from app.config import get_settings
from app.schemas import PodcastHit, PodcastSearchResponse

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"

MB_POST = "2116220650058846455"          # Ansem's gold video, on the MB shelf
MCG_POST = "2115115665753518109"
RASMR_PAIRED = "2090840111306936721"
RASMR_X_ONLY = "2109683904819150927"


# --- the shelf -------------------------------------------------------------

@pytest.fixture
def files(tmp_path, monkeypatch):
    """A small, known set of the five files the shelf is built from."""
    with gzip.open(tmp_path / "summaries.json.gz", "wt") as fh:
        json.dump([{"episode_id": f"x-{MB_POST}", "title": "Ansem on X: gold",
                    "show": "Ansem on X", "published_at": "2026-10-30",
                    "summary": "**Topics**\n- [0:01:40] blow off top\n- [0:04:57] bottom"},
                   {"episode_id": "KcazozbZHd0", "title": "a YouTube one",
                    "summary": "x"}], fh)
    (tmp_path / "broadcast_links.json").write_text(json.dumps(
        {"x-2115904746355851479": "https://x.com/i/broadcasts/1dxYlaZbXWQJX",
         "x-notdigits": "https://x.com/i/broadcasts/abc"}))
    (tmp_path / "mcg.json").write_text(json.dumps({
        "2NK_MBlFvtM": {"broadcast": "b", "post": MCG_POST,
                        "sections": [{"from": 0, "offset": -128, "to": 1418},
                                     {"from": 1961, "offset": -161, "to": None}]},
        "bad": {"broadcast": "b", "post": "../../etc/passwd", "offset": 0}}))
    (tmp_path / "rasmr.json").write_text(json.dumps({
        "2bev0UDp79Y": {"broadcast": "b", "post": RASMR_PAIRED, "offset": -15},
        "WlvYe5EdIJ0": {"broadcast": "b", "post": RASMR_X_ONLY, "offset": -40},
        "noclock0000": {"broadcast": "b", "post": "2099999999999999999"}}))
    (tmp_path / "rasmr_index.json").write_text(json.dumps([
        {"id": f"x-{RASMR_X_ONLY}", "title": "LIVE: Portfolio Update",
         "published_at": "2026-10-12", "seconds": 7539},
        {"id": "2bev0UDp79Y", "title": "a paired stream", "published_at": "2026-08-21"}]))
    (tmp_path / "mcg_index.json").write_text(json.dumps([
        {"id": "2NK_MBlFvtM", "title": "MCG Live: Binance", "published_at": "2026-10-27"}]))
    for name, value in (("SUMMARIES", "summaries.json.gz"),
                        ("BROADCAST_LINKS", "broadcast_links.json"),
                        ("MCG_PAIRS", "mcg.json"), ("RASMR_PAIRS", "rasmr.json"),
                        ("RASMR_SHELF", "rasmr_index.json"),
                        ("MCG_SHELF", "mcg_index.json")):
        monkeypatch.setattr(x_posts, name, tmp_path / value)
    x_posts.shelf.cache_clear()
    x_posts._summaries.cache_clear()
    yield tmp_path
    x_posts.shelf.cache_clear()
    x_posts._summaries.cache_clear()


def test_each_kind_of_post_lands_in_its_archive_on_its_clock(files):
    shelf = x_posts.shelf()
    assert shelf[MB_POST] == {"archive": "podcast", "episode_id": f"x-{MB_POST}",
                              "clock": {"offset": 0}}
    assert shelf["2115904746355851479"]["archive"] == "podcast"
    assert shelf[MCG_POST]["archive"] == "mcg"
    assert shelf[MCG_POST]["clock"]["sections"][0]["offset"] == -128
    assert shelf[MCG_POST]["title"] == "MCG Live: Binance"
    assert shelf[RASMR_PAIRED]["clock"] == {"offset": -15}


def test_a_stream_indexed_from_x_wins_over_its_youtube_pair(files):
    """Its transcript is on X's clock already: offset 0, not the pair's -40."""
    row = x_posts.shelf()[RASMR_X_ONLY]
    assert row["episode_id"] == f"x-{RASMR_X_ONLY}" and row["clock"] == {"offset": 0}


def test_ids_that_are_not_post_ids_never_reach_the_shelf(files):
    shelf = x_posts.shelf()
    assert all(re.fullmatch(r"[0-9]{5,25}", post) for post in shelf)
    assert "KcazozbZHd0" not in json.dumps(x_posts.listing())


def test_a_pair_with_no_measured_clock_gets_a_button_and_no_seeking(files):
    row = x_posts.describe("2099999999999999999")
    assert row["seekable"] is False and row["times"] == {}


def test_one_broken_file_costs_only_its_own_archive(files):
    (files / "mcg.json").write_text("{ not json")
    x_posts.shelf.cache_clear()
    archives = set(x_posts.listing().values())
    assert "mcg" not in archives and {"podcast", "rasmr"} <= archives


# --- the clocks ------------------------------------------------------------

@pytest.mark.parametrize("seconds,clock,expected", [
    (100, {"offset": 0}, 100),
    (100, {"offset": -15}, 85),
    (10, {"offset": -15}, None),          # before X's stream began
    (600, {"sections": [{"from": 0, "offset": -128, "to": 1418}]}, 472),
    (1500, {"sections": [{"from": 0, "offset": -128, "to": 1418},
                         {"from": 1961, "offset": -161, "to": None}]}, None),
    (5000, {"sections": [{"from": 1961, "offset": -161, "to": None}]}, 4839),
    (100, {}, None),
])
def test_a_transcript_second_on_x(seconds, clock, expected):
    assert x_posts.to_x(seconds, clock) == expected


def test_only_real_times_convert():
    times = x_posts.times_in("[1:03:00]–[1:20:00], 14:25, a 3:1 ratio, 9:75, 1:61:00, "
                             "12:30:45:10, 2026-10-30 10:15", {"offset": 0})
    assert times == {"1:03:00": 3780, "1:20:00": 4800, "14:25": 865, "10:15": 615}


def test_the_browser_finds_the_same_times_the_server_converts():
    """Two copies of one pattern, in two languages. If they drift, a time
    the server converted shows as a plain word, or the reverse."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    samples = ["at 1:39:33 and 14:25", "ratio 3:1 and 12:30:45:10", "[0:08:01]–[0:08:16]",
               "10:5 and 100:00 and 1:2:3", "2026-10-30 10:15:00Z", "x1:00 1:00x"]
    script = (f"const M=require({json.dumps(str(EXT / 'lib.js'))});const out={{}};"
              f"for(const s of {json.dumps(samples)}){{out[s]=M.timeTokens(s)}}"
              "console.log(JSON.stringify(out))")
    found = json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                      text=True, timeout=60, check=True).stdout)
    for sample in samples:
        assert found[sample] == x_posts.TIME.findall(sample), sample


def test_the_panel_names_its_post(files):
    row = x_posts.describe(MB_POST)
    assert row["title"] == "Ansem on X: gold" and row["show"] == "Ansem on X"
    assert row["times"] == {"0:01:40": 100, "0:04:57": 297}
    assert row["page"] == f"https://search.lexthedev.com/demo/podcast.html#ep-x-{MB_POST}"
    mcg = x_posts.describe(MCG_POST)
    assert mcg["summary"] is None and mcg["page"].endswith("/demo/mcg.html")
    assert x_posts.describe("1234567") is None


# --- the routes ------------------------------------------------------------

class _Stub:
    def __init__(self, *a, **k):
        pass


class _Archive:
    """A PodcastIndex stand-in that records how it was asked."""

    def __init__(self, answer="Around 0:01:40 he called the top. At 9:99 nothing."):
        self.answer, self.calls = answer, []

    async def search(self, query, top_k=None, instruction=None, model=None,
                     episode_id=None):
        self.calls.append({"query": query, "episode_id": episode_id,
                           "instruction": instruction})
        hit = PodcastHit(episode_id=episode_id, title="t", start_seconds=100.0,
                         timestamp="1:40", deep_link="https://x.com", text="words",
                         score=1.0)
        return PodcastSearchResponse(answer=self.answer, hits=[hit], model="m")


@pytest.fixture
def client(files, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    get_settings.cache_clear()
    for name in ("Retriever", "ConciergeAgent", "IngestionPipeline", "PodcastIndex"):
        monkeypatch.setattr(main_module, name, _Stub)
    with TestClient(main_module.app) as c:
        c.app.state.podcast = _Archive()
        c.app.state.mcg = _Archive("At 30:00 and 1:00:00 they said it.")
        c.app.state.rasmr = None
        c.app.state.answers._store.clear() if hasattr(c.app.state.answers, "_store") else None
        yield c
    get_settings.cache_clear()


def test_the_shelf_is_public_cacheable_and_only_ids(client):
    r = client.get("/v1/x/shelf")
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"
    body = r.json()
    assert body["posts"][MB_POST] == "podcast" and body["count"] == len(body["posts"])
    assert set(body["posts"].values()) <= {"podcast", "mcg", "rasmr"}


def test_a_post_opens_with_its_summary_and_x_seconds(client):
    body = client.get(f"/v1/x/post/{MB_POST}").json()
    assert body["summary"].startswith("**Topics**")
    assert body["times"]["0:04:57"] == 297


@pytest.mark.parametrize("post", ["1234567", "abc", "1" * 30, "..%2F..%2Fetc",
                                  "123456789012345678901234567890", "%00"])
def test_anything_but_a_known_post_is_not_found(client, post):
    assert client.get(f"/v1/x/post/{post}").status_code == 404


def test_a_question_is_answered_from_that_recording_only(client):
    r = client.post("/v1/x/ask", json={"post": MCG_POST, "question": "  what about binance  "})
    assert r.status_code == 200
    call = client.app.state.mcg.calls[-1]
    assert call["episode_id"] == "2NK_MBlFvtM", "the shelf picks the episode"
    assert call["query"] == "what about binance"
    assert "this one recording on X" in call["instruction"]
    # 30:00 is in the minutes the clocks moved; 1:00:00 is 3600 - 161.
    assert r.json()["times"] == {"1:00:00": 3439}


def test_the_caller_cannot_choose_an_episode_or_an_archive(client):
    r = client.post("/v1/x/ask", json={"post": MB_POST, "question": "q",
                                       "episode_id": "SOMETHING_ELSE",
                                       "archive": "elon", "namespace": "elon"})
    assert r.status_code == 200
    assert client.app.state.podcast.calls[-1]["episode_id"] == f"x-{MB_POST}"


@pytest.mark.parametrize("body", [
    {"post": MB_POST, "question": ""},
    {"post": MB_POST, "question": "   \n "},
    {"post": MB_POST, "question": "q" * 501},
    {"post": "abc", "question": "q"},
    {"post": MB_POST + "'; drop", "question": "q"},
    {"post": 2116220650058846455, "question": "q"},
    {"question": "q"},
    {"post": MB_POST},
])
def test_a_malformed_question_is_refused_before_anything_is_spent(client, body):
    assert client.post("/v1/x/ask", json=body).status_code == 422
    assert client.app.state.podcast.calls == []


def test_an_unknown_post_costs_the_caller_nothing(client):
    from app.security import per_client_daily
    per_client_daily._counts.clear()
    r = client.post("/v1/x/ask", json={"post": "1234567", "question": "q"})
    assert r.status_code == 404
    assert sum(per_client_daily._counts.values()) == 0


def test_an_archive_that_is_not_loaded_says_so_and_costs_nothing(client):
    from app.security import per_client_daily
    per_client_daily._counts.clear()
    r = client.post("/v1/x/ask", json={"post": RASMR_PAIRED, "question": "q"})
    assert r.status_code == 503
    assert sum(per_client_daily._counts.values()) == 0


def test_the_same_question_twice_is_answered_once(client):
    for _ in range(2):
        assert client.post("/v1/x/ask", json={"post": MB_POST, "question": "the top"}
                           ).status_code == 200
    assert len(client.app.state.podcast.calls) == 1


def test_a_market_bubble_answer_gets_the_pages_attribution_guard(client, monkeypatch):
    seen = []

    def correct(answer, hits):
        seen.append(answer)
        return answer.replace("he called", "one of the hosts called"), ["demoted"]
    monkeypatch.setattr(main_module.attribution, "correct", correct)
    r = client.post("/v1/x/ask", json={"post": MB_POST, "question": "who called the top"})
    assert seen and "one of the hosts called" in r.json()["answer"]


def test_asking_carries_every_limit_a_search_has():
    route = next(r for r in main_module.app.routes if getattr(r, "path", "") == "/v1/x/ask")
    deps = {d.call for d in route.dependant.dependencies}
    from app.security import (
        daily_budget,
        global_rate_limit,
        per_client_daily,
        public_rate_limit,
    )
    assert {public_rate_limit, global_rate_limit, daily_budget, per_client_daily} <= deps
    for path in ("/v1/x/shelf", "/v1/x/post/{post_id}"):
        route = next(r for r in main_module.app.routes if getattr(r, "path", "") == path)
        assert public_rate_limit in {d.call for d in route.dependant.dependencies}


# --- one episode, every way into the pool -----------------------------------

def test_one_episode_means_every_search_and_every_hit_is_that_episode(monkeypatch):
    import app.podcast as podcast
    from app.podcast import PodcastIndex
    filters, fetched, asked_now = [], [], []

    async def fake_embed(*a, **k):
        return [0.0, 1.0]

    async def about_now(self, q):
        asked_now.append(q)
        return None
    monkeypatch.setattr(podcast, "embed_query", fake_embed)
    monkeypatch.setattr(PodcastIndex, "_about_now", about_now)

    class Match:
        def __init__(self, episode, start):
            self.score = 0.9
            self.metadata = {"episode_id": episode, "start_seconds": start,
                             "title": "t", "url": "https://x.com/a/status/1",
                             "platform": "other", "text": "w"}

    class Index:
        def query(self, **kwargs):
            filters.append(kwargs.get("filter"))

            class R:
                # A filter that misbehaved: another episode comes back too.
                matches = [Match("x-1", 10.0), Match("x-OTHER", 20.0), Match("x-1", 30.0)]
            return R()

        def fetch(self, **kwargs):
            fetched.append(kwargs)
            return None

    class S:
        rerank_candidates = 50
        rerank_model = None
        voyage_model = "v"
        embedding_dimension = 2
        pinecone_read_timeout_seconds = 5
        retrieval_min_score = 0.0
        rerank_narrow_pool = 0
    index = PodcastIndex.__new__(PodcastIndex)
    index._settings, index._voyage, index._namespace = S(), None, "podcast"
    index._index = Index()
    looked_up = []

    class Terms:
        def lookup(self, query):
            looked_up.append(query)
            return ["a" * 32]

        def terms_in(self, query):
            return ["banks"]
    index._terms = Terms()
    hits = asyncio.run(index._retrieve("what did banks say", 5, only_episode="x-1"))
    assert hits and {h.episode_id for h in hits} == {"x-1"}
    assert filters and all("x-1" in json.dumps(f) for f in filters)
    assert not fetched and not looked_up, "no archive-wide exact-word reads for one episode"
    assert not asked_now, "recency does not apply inside one recording"


# --- the extension itself ----------------------------------------------------

def _manifest() -> dict:
    return json.loads((EXT / "manifest.json").read_text())


def test_it_asks_for_no_permission_it_does_not_use():
    m = _manifest()
    assert m["manifest_version"] == 3
    assert "permissions" not in m and "optional_permissions" not in m
    assert m["host_permissions"] == ["https://search.lexthedev.com/*"]
    assert [c["matches"] for c in m["content_scripts"]] == [["https://x.com/*"]]
    for key in ("externally_connectable", "web_accessible_resources",
                "optional_host_permissions", "oauth2", "key", "update_url"):
        assert key not in m, key
    assert m["content_security_policy"]["extension_pages"] == (
        "script-src 'self'; object-src 'none'; base-uri 'none'")


def test_every_file_the_manifest_names_is_there():
    m = _manifest()
    named = [m["background"]["service_worker"], *m["content_scripts"][0]["js"],
             *m["icons"].values()]
    for name in named:
        assert (EXT / name).is_file(), name


_SCRIPTS = ["lib.js", "background.js", "content.js"]


@pytest.mark.parametrize("pattern", [
    r"\.innerHTML\b", r"\.outerHTML\b", r"insertAdjacentHTML", r"document\.write",
    r"\beval\s*\(", r"new\s+Function\b", r"setTimeout\(\s*['\"]", r"setInterval\(\s*['\"]",
    r"DOMParser", r"createContextualFragment", r"\.srcdoc\b", r"http://(?!www\.w3\.org/2000/svg\b)",
    r"localStorage", r"document\.cookie", r"chrome\.storage", r"externally",
])
def test_no_script_parses_markup_runs_strings_or_keeps_data(pattern):
    for name in _SCRIPTS:
        code = (EXT / name).read_text()
        assert not re.search(pattern, code), f"{name}: {pattern}"


def test_only_the_background_talks_to_the_network_and_only_to_our_origin():
    assert "fetch(" not in (EXT / "content.js").read_text()
    assert "XMLHttpRequest" not in "".join((EXT / n).read_text() for n in _SCRIPTS)
    background = (EXT / "background.js").read_text()
    assert background.count("fetch(") == 1
    assert "fetch(MBS.API + path" in background
    # The only addresses written plainly anywhere in the scripts: ours, and
    # x.com in the check on who is sending a message. (X's patterns in
    # lib.js are escaped regex, not addresses.)
    assert set(re.findall(r"https?://[a-z0-9.-]+", "".join(
        (EXT / n).read_text() for n in _SCRIPTS).replace("http://www.w3.org/2000/svg", ""))) == {
        "https://search.lexthedev.com", "https://x.com"}


def test_the_background_answers_only_its_own_script_on_x():
    background = (EXT / "background.js").read_text()
    assert "sender.id !== chrome.runtime.id" in background
    assert 'return origin === "https://x.com";' in background
    assert 'credentials: "omit"' in background and 'redirect: "error"' in background


def test_the_extensions_own_tests_pass():
    """CI runs pytest, so the JavaScript runs here too."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    run = subprocess.run(["node", "--test", *map(str, sorted((EXT / "test").glob("*.test.js")))],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]

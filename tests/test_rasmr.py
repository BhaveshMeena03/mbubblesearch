"""rasmr's archive: the routes, the X twins, the page, the front door, and
the engine.

The shelf and the pairs are read from the real files, so a pair that
points at something that is not a stream on the shelf, or a broadcast
indexed twice, turns a test red before it reaches a page.
"""

import json
import re
from pathlib import Path

import pytest

from app import podcast
from app.schemas import PodcastHit

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "demo" / "rasmr.html").read_text()
SHELF = json.loads((ROOT / "data" / "rasmr_index.json").read_text())
PAIRS = json.loads((ROOT / "data" / "rasmr_broadcast_links.json").read_text())
CASTS = json.loads((ROOT / "data" / "rasmr_x_broadcasts.json").read_text())["broadcasts"]
PLAYER = re.compile(r"https://x\.com/i/broadcasts/[A-Za-z0-9]+")


# --- the files ---------------------------------------------------------------------

def test_the_shelf_has_no_recording_twice():
    ids = [r["id"] for r in SHELF]
    assert len(ids) == len(set(ids))
    assert len(SHELF) >= 100


def test_every_pair_is_a_youtube_stream_on_the_shelf():
    rows = {r["id"]: r for r in SHELF}
    for vid, pair in PAIRS.items():
        assert vid in rows, vid
        assert rows[vid]["format"] == "stream" and rows[vid].get("source") != "x", vid
        assert PLAYER.fullmatch(pair["broadcast"]), pair
        assert isinstance(pair["offset"], int) and abs(pair["offset"]) <= 600, pair


def test_no_broadcast_is_paired_twice_or_also_indexed_on_its_own():
    """A broadcast that is the twin of a YouTube stream must not also be on
    the shelf as an X-only recording: that is the same stream twice."""
    paired = [p["broadcast"] for p in PAIRS.values()]
    assert len(paired) == len(set(paired))
    on_its_own = {r["url"] for r in SHELF if r.get("source") == "x"}
    assert not on_its_own & set(paired)


def test_every_x_only_recording_is_filed_like_market_bubbles():
    posts = {c["post"] for c in CASTS}
    for row in (r for r in SHELF if r.get("source") == "x"):
        assert re.fullmatch(r"x-\d+", row["id"]), row["id"]
        assert row["id"][2:] in posts, row["id"]
        assert PLAYER.fullmatch(row["url"]), row["url"]
        assert row["format"] == "stream" and row["seconds"] >= 20 * 60


def test_every_broadcast_read_off_x_is_a_player_address():
    assert len(CASTS) >= 30
    for cast in CASTS:
        assert PLAYER.fullmatch(cast["broadcast"]), cast
        assert re.fullmatch(r"\d+", cast["post"]), cast


# --- the routes --------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app import main
    with TestClient(main.app) as found:
        yield found


def test_the_archive_reports_the_shelf(client):
    reply = client.get("/v1/rasmr/archive")
    assert reply.status_code == 200
    body = reply.json()
    assert body["episodes"] == len(SHELF)
    assert body["hours"] == round(sum(r["seconds"] for r in SHELF) / 3600, 1)
    assert body["on_x"] == len(PAIRS) + sum(1 for r in SHELF if r.get("source") == "x")


def test_the_episodes_are_newest_first_and_carry_their_x_twin(client):
    rows = client.get("/v1/rasmr/episodes").json()
    assert len(rows) == len(SHELF)
    days = [r["published_at"] for r in rows]
    assert days == sorted(days, reverse=True)
    for row in rows:
        pair = PAIRS.get(row["episode_id"])
        if pair:
            assert row["x_broadcast"] == pair["broadcast"]
            assert row["x_offset"] == pair["offset"]
        else:
            assert "x_broadcast" not in row


def test_a_pair_that_is_not_a_player_address_is_not_offered(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "_RASMR_LINKS_CACHE", {
        "bad": {"broadcast": "javascript:alert(1)", "offset": 2},
        "elsewhere": {"broadcast": "https://evil.example/i/broadcasts/abc", "offset": 0},
        "odd": {"broadcast": "https://x.com/i/broadcasts/abcDEF1", "offset": "soon"},
        "good": {"broadcast": "https://x.com/i/broadcasts/abcDEF1", "offset": -3}})
    assert main._rasmr_on_x("bad") == {}
    assert main._rasmr_on_x("elsewhere") == {}
    assert main._rasmr_on_x("odd") == {}
    assert main._rasmr_on_x("missing") == {}
    assert main._rasmr_on_x("good") == {"x_broadcast": "https://x.com/i/broadcasts/abcDEF1",
                                        "x_offset": -3}


def test_there_are_no_notes_yet_and_the_page_is_told_so(client):
    assert client.get("/v1/rasmr/summaries").json() == {"count": 0, "summaries": {}}


def test_the_short_address_opens_the_page(client):
    reply = client.get("/rasmr", follow_redirects=False)
    assert reply.status_code == 301 and reply.headers["location"] == "/demo/rasmr.html"


# --- the page ----------------------------------------------------------------------

def test_the_page_is_offered_to_search_engines():
    """It went up marked noindex for a look first. Launched, it is a page
    like the other archives, and nothing keeps it out of a search."""
    assert 'name="robots"' not in PAGE
    assert "noindex" not in PAGE


def test_the_page_is_linked_like_the_other_archives():
    """It was linked from nowhere until rasmr said yes. Now it is wherever
    ThreadGuy's is: the switcher on every page, the front door's channel
    card and footer, ThreadGuy's footer and llms.txt."""
    demo = ROOT / "demo"
    nav = (demo / "nav.js").read_text()
    assert '{ href: "/rasmr",' in nav
    assert 'if (href === "/rasmr") return /rasmr/.test(path);' in nav
    home = (demo / "home.html").read_text()
    assert '{k:"rasmr", name:"rasmr", href:"/rasmr"' in home     # the channel card
    assert '<a href="/rasmr">rasmr</a>' in home.split("<footer>")[1]
    tg = (demo / "threadguy.html").read_text()
    assert '<a href="/rasmr">rasmr</a>' in tg.split("<footer>")[1]
    llms = (demo / "llms.txt").read_text()
    assert "https://search.lexthedev.com/rasmr : " in llms
    assert "/v1/rasmr/search" in llms and "/v1/rasmr/archive" in llms


# --- the front door ----------------------------------------------------------------

def test_he_is_a_room_in_the_fan_out():
    from app.main import _ROOMS
    assert ("rasmr", "rasmr", "/rasmr") in _ROOMS


def probe(key, score):
    return {"key": key, "label": key, "href": "/", "score": score, "hits": [1]}


def test_naming_him_wins_a_near_tie():
    from app.main import _prefer_named
    probes = [probe("threadguy", 0.52), probe("podcast", 0.50), probe("rasmr", 0.47)]
    assert _prefer_named(probes, "why did rasmr sell all his hype")[0]["key"] == "rasmr"
    assert _prefer_named(probes, "what did @rasmr_eth say about hype")[0]["key"] == "rasmr"
    # but not when his archive is far behind
    far = [probe("podcast", 0.70), probe("rasmr", 0.40)]
    assert _prefer_named(far, "what did rasmr say about zcash")[0]["key"] == "podcast"


def test_ansem_and_elon_are_not_taken_by_him():
    """The ThreadGuy lesson the host rule was written for: a question that
    names Ansem is asking about the show, and one that names Elon about
    Elon, whichever archive scores a little higher."""
    from app.main import _prefer_named
    probes = [probe("rasmr", 0.53), probe("podcast", 0.50), probe("elon", 0.49)]
    assert _prefer_named(probes, "what is ansem's price target for zcash")[0]["key"] \
        == "podcast"
    assert _prefer_named(probes, "what did elon say about mars")[0]["key"] == "elon"
    # Both named: the scores decide, as with ThreadGuy.
    assert _prefer_named(probes, "what did ansem say about rasmr") == probes


def test_naming_his_archive_wins_outright():
    from app.main import _prefer_named
    probes = [probe("podcast", 0.62), probe("rasmr", 0.41)]
    for q in ("what did ansem say on rasmr's stream", "in the rasmr archive, who is cupsey"):
        assert _prefer_named(probes, q)[0]["key"] == "rasmr", q


def test_the_front_door_counts_him_in_its_totals(client, monkeypatch):
    from app import main

    async def show(_summaries):
        return {"shows": 21, "hours": 73}
    monkeypatch.setattr(main, "podcast_archive", show)
    client.app.state.archive_sizes = None
    body = client.get("/v1/archives").json()
    client.app.state.archive_sizes = None
    assert body["archives"] == 6
    assert body["by"]["rasmr"]["count"] == len(SHELF)
    assert body["by"]["rasmr"]["hours"] == round(sum(r["seconds"] for r in SHELF) / 3600, 1)
    assert body["recordings"] == sum(a["count"] for a in body["by"].values())


def test_the_example_question_and_the_card_on_the_front_door_are_his():
    home = (ROOT / "demo" / "home.html").read_text()
    tries = home.split("var TRIES = [")[1].split("];")[0]
    assert '{k:"rasmr",' in tries
    # The card is one of his own page's, checked when it was chosen.
    card = home.split("var MOMENTS = [")[1].split("];")[0].split('{k:"rasmr", ')[1]
    vid, at = re.search(r'vid:"([\w-]{11})", at:(\d+)', card).groups()
    said = re.search(r'said:"([^"]+)"', card).group(1)
    ours = (ROOT / "demo" / "moments.js").read_text().split("    rasmr: [")[1]
    assert f'{{vid: "{vid}", at: {at}, who: "rasmr",' in ours
    assert f'said: "{said}"' in ours


def test_the_page_asks_its_own_archive_and_nobody_elses():
    for route in ("/v1/rasmr/search/stream", "/v1/rasmr/search", "/v1/rasmr/archive",
                  "/v1/rasmr/episodes", "/v1/rasmr/summaries"):
        assert f'"{route}"' in PAGE, route
    assert "/v1/threadguy" not in PAGE
    assert "clip" not in PAGE.split("<script>")[-1].lower()


def test_the_page_says_it_is_independent_and_uses_no_dashes():
    assert "not affiliated with rasmr or his show" in PAGE
    assert "—" not in PAGE and "–" not in PAGE


def test_the_page_only_opens_x_at_a_player_address():
    assert r"/^https:\/\/x\.com\/i\/broadcasts\/[A-Za-z0-9]+$/" in PAGE


# --- the engine --------------------------------------------------------------------

def hit(title: str, n: int) -> PodcastHit:
    return PodcastHit(episode_id=f"v{n}", title=title, start_seconds=60.0 * n,
                      timestamp=f"{n}:00", deep_link="https://www.youtube.com/watch?v=x",
                      text="words", score=0.5)


def test_rasmr_has_his_own_prompt_and_host_rule():
    assert "rasmr" in podcast.HOSTED_CHANNELS
    assert "rasmr's streams and interviews" in podcast.RASMR_SYSTEM_PROMPT
    assert "—" not in podcast.RASMR_SYSTEM_PROMPT.split("Rules:")[0]


def test_a_question_about_his_view_is_answered_from_his_own_streams():
    """The ThreadGuy lesson, for rasmr: a guest's line is not the host's."""
    hits = [hit("LIVE: I Sold All My HYPE | Ansem Is Candling", 1),
            hit("LIVE: My New Trading Strategy | Trenching Live", 2),
            hit("LIVE: Talkin Markets | Up Too Much", 3),
            hit("Qwerty - How to SURVIVE Trading Crypto in 2026", 4)]
    asks = podcast.HOSTED_CHANNELS["rasmr"]
    kept = podcast.host_view_only("what does rasmr think of hype", hits, asks)
    assert [h.episode_id for h in kept] == ["v1", "v2", "v3"]
    # A question about the guests keeps the interviews.
    assert podcast.host_view_only("who did rasmr interview about trading", hits, asks) == hits
    # And ThreadGuy's rule is not triggered by rasmr's name.
    assert podcast.host_view_only("what does rasmr think of hype", hits) == hits

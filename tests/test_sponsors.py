"""The sponsor list: every time Polymarket or Phantom was said on air.

What is shipped (demo/sponsor-mentions.json) is held to the transcripts it
was built from, which are committed (data/episodes.json.gz). A new episode
landing does not make it wrong, only older, so the test asks that every
line shipped is in its transcript word for word at its second, and that
rebuilding the shows it covers gives exactly what was shipped.
"""

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = json.loads((ROOT / "demo" / "sponsor-mentions.json").read_text())
EPISODES = json.loads(gzip.decompress((ROOT / "data" / "episodes.json.gz").read_bytes()))
BROADCASTS = json.loads((ROOT / "data" / "broadcast_links.json").read_text())


def builder():
    spec = importlib.util.spec_from_file_location("build_sponsors",
                                                  ROOT / "scripts" / "build_sponsors.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def show(eid, *lines):
    return {"episode_id": eid, "title": "Episode", "platform": "other", "show": None,
            "segments": [{"t": float(t), "text": text} for t, text in lines]}


def test_every_shipped_line_is_in_its_transcript_at_its_second():
    by_id = {e["episode_id"]: e for e in EPISODES}
    for ep in SHIPPED["episodes"]:
        segs = {(round(float(s["t"]), 1), s["text"].strip()) for s in by_id[ep["episode_id"]]["segments"]}
        for line in ep["lines"]:
            assert (line["t"], line["text"]) in segs, (ep["episode_id"], line["t"])


def test_rebuilding_the_shows_it_covers_gives_what_was_shipped():
    b = builder()
    read = [e for e in b.shows(EPISODES) if e["episode_id"] in set(SHIPPED["read"])]
    assert len(read) == SHIPPED["shows_read"] == len(SHIPPED["read"])
    again = b.build(read, BROADCASTS)
    assert {k: again[k] for k in ("sponsors", "episodes", "left_out", "read")} == {
        k: SHIPPED[k] for k in ("sponsors", "episodes", "left_out", "read")}


def test_the_totals_add_up_from_the_lines():
    for s in SHIPPED["sponsors"]:
        lines = [line for e in SHIPPED["episodes"] for line in e["lines"] if line["sponsor"] == s["key"]]
        assert sum(line["count"] for line in lines) == s["mentions"]
        assert len({e["episode_id"] for e in SHIPPED["episodes"]
                    if any(line["sponsor"] == s["key"] for line in e["lines"])}) == s["shows"]


def test_both_spellings_count_and_each_name_counts_once_per_saying():
    got = builder().build([show("x-2", (10, "Um, but yeah, W phantom W poly market."),
                                (20, "Like even polymarket, polymarket offers perps, right?"))], {})
    totals = {s["key"]: s["mentions"] for s in got["sponsors"]}
    assert totals == {"polymarket": 3, "phantom": 1}


def test_a_drone_called_phantom_is_left_out_and_said_so():
    got = builder().build([show("x-2", (10, "I took that money and I bought a DJI Phantom One drone."),
                                (20, "Thank you, Phantom, our lovely partner."))], {})
    assert {s["key"]: s["mentions"] for s in got["sponsors"]}["phantom"] == 1
    assert got["left_out"][0]["why"] == "a DJI Phantom drone, not the wallet"


def test_kalshi_is_not_polymarket():
    got = builder().build([show("x-2", (10, "Well, Cal, she says there's a 17% chance"))], {})
    assert all(s["mentions"] == 0 for s in got["sponsors"]) and not got["episodes"]


def test_ansems_own_videos_are_not_the_show():
    video = {**show("x-3", (10, "Polymarket.")), "show": "Ansem on X"}
    assert builder().build([video], {})["episodes"] == []


def test_a_line_links_to_its_second_on_the_broadcast_when_there_is_one():
    b = builder()
    assert b.link("x-1", 293.6, {"x-1": "https://x.com/i/broadcasts/abc"}) == (
        "https://x.com/i/broadcasts/abc?t=293", True)
    assert b.link("x-2092001067471147370", 50, {}) == (
        "https://x.com/i/status/2092001067471147370", False)


def test_the_date_is_the_day_it_aired_in_new_york():
    # The 8 Oct 2026 show went out at 8:54 pm Eastern, past midnight UTC.
    assert builder().aired("x-2108360500283335093").date().isoformat() == "2026-10-09"
    dates = {e["episode_id"]: e["date"] for e in SHIPPED["episodes"]}
    assert dates["x-2108360500283335093"] == "2026-10-08"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import app.main as main
    with TestClient(main.app) as c:
        yield c


def test_the_page_is_there_unlinked_and_kept_out_of_search(client):
    moved = client.get("/sponsors", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/demo/sponsors.html"
    page = client.get("/demo/sponsors.html")
    assert page.status_code == 200
    assert '<meta name="robots" content="noindex, nofollow">' in page.text
    assert client.get("/demo/sponsor-mentions.json").status_code == 200
    for other in (ROOT / "demo").glob("*.html"):
        if other.name != "sponsors.html":
            assert "/sponsors" not in other.read_text(encoding="utf-8"), other.name


def test_the_page_has_no_dashes_in_its_copy():
    text = (ROOT / "demo" / "sponsors.html").read_text(encoding="utf-8")
    assert "—" not in text and "–" not in text

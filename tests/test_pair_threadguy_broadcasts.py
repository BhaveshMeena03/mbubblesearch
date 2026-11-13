"""scripts/pair_threadguy_broadcasts.py: reading his broadcast posts and
choosing which show to listen for. The listening itself is
pair_mcg_broadcasts's, tested where it lives."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import pair_threadguy_broadcasts as tg  # noqa: E402

LINKED = {"urls": [{"expanded_url": "https://x.com/i/broadcasts/1pJdRDqeOYnKW"}]}


def post(pid, text, created="2026-10-30T12:43:51.000Z", entities=LINKED):
    return {"id": pid, "text": text, "created_at": created, "entities": entities}


def test_a_title_is_his_words_without_the_link_x_adds():
    assert tg.title_of("MARKET OPEN: FOMC TODAY, Anthropic SAVED the AI &amp; Crypto Trade "
                       "https://t.co/YrQxjoKJAM") == \
        "MARKET OPEN: FOMC TODAY, Anthropic SAVED the AI & Crypto Trade"


def test_only_a_post_that_links_a_broadcast_is_one():
    found = tg.as_broadcast(post("2116149081211048039", "MARKET OPEN: Binance https://t.co/x"))
    assert found == {"broadcast": "https://x.com/i/broadcasts/1pJdRDqeOYnKW",
                     "post": "2116149081211048039", "posted": "2026-10-30T12:43:51.000Z",
                     "title": "MARKET OPEN: Binance"}
    assert tg.as_broadcast(post("1", "gm", entities={"urls": []})) is None


def test_a_second_search_adds_without_repeating():
    a = {"post": "2", "broadcast": "b2"}
    b = {"post": "10", "broadcast": "b10"}
    merged = tg.merge([b, a], [{"post": "2", "broadcast": "b2-again"},
                               {"post": "3", "broadcast": "b3"}])
    assert [m["post"] for m in merged] == ["2", "3", "10"], "by post id as a number"
    assert merged[0]["broadcast"] == "b2-again"


SHOWS = [
    {"id": "EOkDv8qm8ik", "title": "MARKET OPEN: Binance Suffers the BIGGEST HACK EVER",
     "published_at": "2026-10-30"},
    {"id": "QVuAQw_gG6A", "title": "BINANCE HACK: EMERGENCY STREAM", "published_at": "2026-10-29"},
    {"id": "old", "title": "MARKET OPEN: a week earlier", "published_at": "2026-10-23"},
]


def test_the_same_title_is_listened_for_alone():
    cast = {"title": "BINANCE HACK: EMERGENCY STREAM", "posted": "2026-10-29T21:13:00Z"}
    assert [r["id"] for r in tg.candidates(cast, SHOWS, {})] == ["QVuAQw_gG6A"]


def test_a_retitled_upload_is_found_by_its_day_nearest_first():
    cast = {"title": "MARKET OPEN: Binance after BIGGEST HACK EVER", "posted": "2026-10-30T12:43:51Z"}
    assert [r["id"] for r in tg.candidates(cast, SHOWS, {})] == ["EOkDv8qm8ik", "QVuAQw_gG6A"]
    assert [r["id"] for r in tg.candidates(cast, SHOWS, {"EOkDv8qm8ik": {}})] == ["QVuAQw_gG6A"], \
        "a show already paired is not heard again"


def test_a_show_with_no_date_is_never_a_near_one():
    cast = {"title": "x", "posted": "2026-10-30T00:00:00Z"}
    assert tg.candidates(cast, [{"id": "nodate", "title": "y"}], {}) == []


def test_pairing_by_hand_takes_only_an_x_broadcast_and_a_shelved_show(tmp_path, monkeypatch, capsys):
    """It listens to whatever it is given, so what it is given is checked
    first. Nothing here reaches the network."""
    import json

    shelf = tmp_path / "shelf.json"
    shelf.write_text(json.dumps([{"id": "B8A-Gf9Strs", "title": "MARKET OPEN"}]))
    monkeypatch.setattr(tg, "SHELF", shelf)
    monkeypatch.setattr(tg, "OUT", tmp_path / "pairs.json")
    monkeypatch.setattr(tg, "measure", lambda url, row: {"offset": 4})
    assert tg.pair_by_hand("B8A-Gf9Strs", "https://evil.example/i/broadcasts/1MJgNbOYoZkGL") == 1
    assert tg.pair_by_hand("B8A-Gf9Strs", "file:///etc/passwd") == 1
    assert tg.pair_by_hand("notshelved0", "https://x.com/i/broadcasts/1MJgNbOYoZkGL") == 1
    assert not (tmp_path / "pairs.json").exists()
    assert tg.pair_by_hand("B8A-Gf9Strs", "https://x.com/i/broadcasts/1MJgNbOYoZkGL?t=5") == 0
    assert json.loads((tmp_path / "pairs.json").read_text()) == {
        "B8A-Gf9Strs": {"broadcast": "https://x.com/i/broadcasts/1MJgNbOYoZkGL", "offset": 4}}


def test_a_broadcast_that_is_not_the_show_by_ear_is_left_alone(tmp_path, monkeypatch):
    import json

    shelf = tmp_path / "shelf.json"
    shelf.write_text(json.dumps([{"id": "B8A-Gf9Strs", "title": "MARKET OPEN"}]))
    monkeypatch.setattr(tg, "SHELF", shelf)
    monkeypatch.setattr(tg, "OUT", tmp_path / "pairs.json")
    monkeypatch.setattr(tg, "measure", lambda url, row: None)
    assert tg.pair_by_hand("B8A-Gf9Strs", "https://x.com/i/broadcasts/1MJgNbOYoZkGL") == 1
    assert not (tmp_path / "pairs.json").exists()


def test_the_laptops_lines_come_before_youtube_and_the_index(tmp_path, monkeypatch):
    """Cached lines are used without hitting YouTube or Pinecone."""
    asked = []
    cached = {"kept": [{"t": 0.0, "text": "gm"}]}
    monkeypatch.setattr(tg.local_cache, "load_lines",
                        lambda archive, vid: asked.append(("cache", archive)) or cached.get(vid))
    monkeypatch.setattr(tg, "captions", lambda vid, work: asked.append("youtube") or [])
    monkeypatch.setattr(tg, "indexed", lambda vid: asked.append("index") or [{"t": 1.0, "text": "x"}])

    assert tg.transcript("kept", tmp_path) == [{"t": 0.0, "text": "gm"}]
    assert asked == [("cache", "threadguy")]

    asked.clear()
    assert tg.transcript("new", tmp_path) == [{"t": 1.0, "text": "x"}]
    assert asked == [("cache", "threadguy"), "youtube", "index"]

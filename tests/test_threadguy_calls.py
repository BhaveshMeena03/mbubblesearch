"""Tests for the ThreadGuy calls board (scripts/build_threadguy_calls.py).

Quote matching and the trade filter are covered in test_calls.py; these cover
the X check fields, the YouTube fallback, hourly pricing, the shipped file
and the route.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import calls, local_cache  # noqa: E402
from scripts import build_threadguy_calls as tg  # noqa: E402

DATA = ROOT / "data"
LINES = [{"t": 2570.0, "text": "so that's really what i want to know because"},
         {"t": 2582.6, "text": "bitcoin is ready to pop zcash is ready to double"},
         {"t": 2590.0, "text": "look i'm still not fully convinced on the rest of the asset class"}]
CANDIDATE = {"id": "i2ArP1s0pOw:1:zec", "episode_id": "i2ArP1s0pOw", "line": 1, "last_line": 1,
             "start_seconds": 2582.6, "speaker": "ThreadGuy", "asset": "Zcash", "symbol": "ZEC",
             "asset_class": "crypto", "stance": "long", "kind": "pick", "target": "",
             "quote": "bitcoin is ready to pop zcash is ready to double"}
FOUND = {"shows": {"i2ArP1s0pOw": {"candidates": [CANDIDATE]}}}
CHECKED = {"keep": True, "why": "his own view", "broadcast": "1MJgNgENXbAGL",
           "x_second": 2582, "on_air": "2026-05-05T19:32:00Z",
           "checked": "heard at 2582 on X, alone on camera"}
# 19 Feb 2026: a YouTube live with no X broadcast posted 19 to 22 Feb
ON_YOUTUBE = {"keep": True, "why": "his own position", "youtube": True,
              "on_air": "2026-02-19T20:36:40Z", "no_x": "no broadcast posted 19-22 Feb",
              "checked": "frame at 491s on YouTube: him alone at his desk"}


def test_a_kept_row_carries_where_it_was_checked_on_x():
    row, = tg.kept_calls(FOUND, {CANDIDATE["id"]: CHECKED})
    assert (row["broadcast"], row["x_second"], row["speaker"]) == ("1MJgNgENXbAGL", 2582, "ThreadGuy")


@pytest.mark.parametrize("missing", tg.CHECKED)
def test_a_row_nobody_checked_on_x_does_not_ship(missing):
    verdict = {k: v for k, v in CHECKED.items() if k != missing}
    with pytest.raises(SystemExit, match="checked on X"):
        tg.kept_calls(FOUND, {CANDIDATE["id"]: verdict})


def test_the_broadcast_has_to_be_an_id_and_not_a_link_or_a_guess():
    for wrong in ("https://x.com/i/broadcasts/1MJgNgENXbAGL", "?", "1MJg NgENX"):
        with pytest.raises(SystemExit, match="not a broadcast id"):
            tg.kept_calls(FOUND, {CANDIDATE["id"]: dict(CHECKED, broadcast=wrong)})


def test_a_short_needs_its_reason_and_a_stale_verdict_stops_the_build():
    short = dict(CANDIDATE, stance="short")
    with pytest.raises(SystemExit, match="a short needs"):
        tg.kept_calls({"shows": {"x": {"candidates": [short]}}},
                      {short["id"]: dict(CHECKED, why="")})
    with pytest.raises(SystemExit, match="match no candidate"):
        tg.kept_calls(FOUND, {"gone:1:zec": CHECKED})


def test_a_stream_that_never_aired_on_x_is_kept_on_youtube_at_the_second():
    row, = tg.kept_calls(FOUND, {CANDIDATE["id"]: ON_YOUTUBE})
    assert row["youtube"] and "broadcast" not in row
    placed = tg.placed(row, LINES)
    assert placed["x_second"] == placed["start_seconds"] == 2582.6
    assert tg.link(placed) == "https://www.youtube.com/watch?v=i2ArP1s0pOw&t=2581s"
    assert tg.said_at(placed) == datetime(2026, 2, 19, 21, 19, 42, 600000, tzinfo=UTC)


@pytest.mark.parametrize("missing", tg.CHECKED_YOUTUBE)
def test_a_youtube_row_says_when_it_aired_why_not_x_and_what_the_frame_showed(missing):
    verdict = {k: v for k, v in ON_YOUTUBE.items() if k != missing}
    with pytest.raises(SystemExit, match="kept off X without"):
        tg.kept_calls(FOUND, {CANDIDATE["id"]: verdict})


def test_a_stream_that_did_air_on_x_links_there_and_not_to_youtube():
    with pytest.raises(SystemExit, match="links there"):
        tg.kept_calls(FOUND, {CANDIDATE["id"]: dict(ON_YOUTUBE, broadcast="1MJgNgENXbAGL")})


def test_a_rejected_candidate_needs_no_x_check():
    assert tg.kept_calls(FOUND, {CANDIDATE["id"]: {"keep": False, "why": "read from chat"}}) == []


def test_the_quote_has_to_be_on_its_line_and_not_a_trade():
    placed = tg.placed(dict(CANDIDATE), LINES)
    assert placed["quote"] == "bitcoin is ready to pop zcash is ready to double"
    assert placed["start_seconds"] == 2582.6
    assert tg.placed(dict(CANDIDATE, quote="bitcoin is ready to pop zcash is going to triple"),
                     LINES) is None
    # Said a line away is only allowed for a row a person re-cut.
    assert tg.placed(dict(CANDIDATE, line=2), LINES) is None
    assert tg.placed(dict(CANDIDATE, line=2, recut=True), LINES) is not None
    stop = [{"t": 1.0, "text": "a little baby nibble at 249 i'm out below 242"}]
    assert tg.placed(dict(CANDIDATE, line=0, quote="a little baby nibble at 249 i'm out below 242"),
                     stop) is None


def bars(start: datetime, closes: list[float]):
    return [(start + timedelta(hours=i), None, c) for i, c in enumerate(closes)]


def test_a_call_is_priced_at_the_last_hour_that_had_ended_when_he_said_it():
    said = datetime(2026, 5, 5, 20, 15, tzinfo=UTC)
    hours = bars(datetime(2026, 5, 5, 18, tzinfo=UTC), [470.0, 463.0, 516.0])
    # 18:00 and 19:00 had ended; the 20:00 bar he spoke in closes after him.
    assert tg.close_before(hours, said) == 463.0
    assert tg.close_before(hours, datetime(2026, 5, 5, 18, 30, tzinfo=UTC)) is None
    assert tg.close_before(hours, said + timedelta(days=7)) is None


def test_a_call_on_easter_monday_is_priced_at_thursdays_close():
    """LVMH on 6 Apr 2026, 20:08 UTC: Euronext shut Good Friday and Easter Monday."""
    thursday = bars(datetime(2026, 4, 2, 13, tzinfo=UTC), [612.0, 615.5, 614.2])
    assert tg.close_before(thursday, datetime(2026, 4, 6, 20, 8, tzinfo=UTC)) == 614.2


def test_the_moment_is_the_broadcast_start_plus_the_second_and_links_to_it():
    row = dict(CHECKED, x_second=2582.6)
    assert tg.said_at(row) == datetime(2026, 5, 5, 20, 15, 2, 600000, tzinfo=UTC)
    assert tg.link(row) == "https://x.com/i/broadcasts/1MJgNgENXbAGL?t=2581"
    assert tg.link(dict(row, x_second=0.4)).endswith("?t=0")


# --- the file the site serves --------------------------------------------------

def shipped() -> list[dict]:
    return json.loads((DATA / "threadguy_calls.json").read_text())["calls"]


def verdicts() -> dict:
    return json.loads((DATA / "threadguy_calls_verdicts.json").read_text())


def test_every_candidate_has_been_read():
    """New candidates need a verdict before the board can ship."""
    found = json.loads((DATA / "threadguy_calls_candidates.json").read_text())
    unread = [c["id"] for show in found["shows"].values() for c in show["candidates"]
              if c["id"] not in verdicts()]
    assert not unread, unread[:5]


def test_every_row_was_kept_and_checked_on_x():
    kept = {i: v for i, v in verdicts().items() if v.get("keep")}
    board = shipped()
    assert {r["id"] for r in board} == set(kept) and len(board) == len(kept)
    for r in board:
        v = kept[r["id"]]
        if v.get("youtube"):
            assert v["no_x"] and not v.get("broadcast"), r["id"]
            at = max(0, int(r["youtube_seconds"]) - 1)
            assert r["link"] == f"https://www.youtube.com/watch?v={r['episode_id']}&t={at}s"
            continue
        assert r["link"] == f"https://x.com/i/broadcasts/{v['broadcast']}?t={max(0, int(v['x_second']) - 1)}"
        assert r["speaker"] == "ThreadGuy" and r["source"] == "show"
        assert r["price_then"] and r["listing"], r["id"]


def test_every_rejection_says_why():
    assert all(str(v.get("why") or "").strip() for v in verdicts().values())


def test_he_is_on_the_board_once_per_asset_per_stream_per_side():
    seen = [(r["episode_id"], r["listing"], r["stance"], r["kind"]) for r in shipped()]
    assert len(seen) == len(set(seen))


def test_the_misses_are_on_the_board_and_no_trade_is():
    moved = [calls.moved(r["stance"], r["price_then"], r["price_last"])
             for r in shipped() if r["kind"] != "exit"]
    assert any(m is not None and m < 0 for m in moved)
    assert not [r["id"] for r in shipped() if calls.tactical(r["quote"])]


@pytest.mark.skipif(not local_cache.load_lines("threadguy", "i2ArP1s0pOw"),
                    reason="his transcripts live on the laptop, not in the repo")
def test_every_row_is_said_word_for_word_on_the_laptops_transcript():
    for r in shipped():
        lines = local_cache.load_lines("threadguy", r["episode_id"])
        assert lines, r["id"]
        span = calls.locate(r["quote"], lines, r["line"])
        assert span is not None and span.first == r["line"], r["id"]
        assert tg.undash(calls.cut(lines, span)) == r["quote"], r["id"]
        assert float(lines[r["line"]]["t"]) == r["youtube_seconds"], r["id"]


def test_the_route_serves_his_board_and_the_page_is_reachable(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    async def down(listings, *, client):
        raise TimeoutError("price source is down")
    monkeypatch.setattr(main.call_prices, "now", down)
    monkeypatch.setattr(main, "_CALL_PRICES", {})
    with TestClient(main.app) as client:
        reply = client.get("/v1/threadguy/calls")
        assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
        body = reply.json()
        assert len(body["calls"]) == len(shipped()) and list(body["by_speaker"]) == ["ThreadGuy"]
        assert all(r["price_now"] and "listing" not in r for r in body["calls"])
        moved = client.get("/threadguy/calls", follow_redirects=False)
        assert moved.status_code == 301 and moved.headers["location"] == "/demo/threadguy-calls.html"
        assert client.get("/demo/threadguy-calls.html").status_code == 200


def test_a_board_that_is_not_there_is_a_503(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setattr(main, "_THREADGUY_CALLS_FILE", tmp_path / "nothing.json")
    with TestClient(main.app) as client:
        assert client.get("/v1/threadguy/calls").status_code == 503


def test_one_boards_fresh_prices_are_not_handed_to_the_other(monkeypatch):
    import asyncio

    import app.main as main

    asked = []

    async def answers(listings, *, client):
        asked.append(sorted(listings))
        return {x: 1.0 for x in listings}
    monkeypatch.setattr(main.call_prices, "now", answers)
    monkeypatch.setattr(main, "_CALL_PRICES", {})

    async def both():
        a = await main._call_prices_now(["BTC-USD", "ETH-USD"])
        b = await main._call_prices_now(["ZEC-USD", "MU"])
        again = await main._call_prices_now(["ETH-USD", "BTC-USD", ""])
        return a, b, again
    a, b, again = asyncio.run(both())
    assert set(a[0]) == {"BTC-USD", "ETH-USD"} and set(b[0]) == {"ZEC-USD", "MU"}
    assert again == a and asked == [["BTC-USD", "ETH-USD"], ["MU", "ZEC-USD"]]


PAGE = (ROOT / "demo" / "threadguy-calls.html").read_text()


def test_the_page_waits_for_his_ok_and_reads_only_its_own_board():
    assert '<meta name="robots" content="noindex">' in PAGE
    assert 'fetch(API + "/v1/threadguy/calls"' in PAGE and '"/v1/calls"' not in PAGE
    for page in ("threadguy.html", "podcast.html", "home.html"):
        assert "/threadguy/calls" not in (ROOT / "demo" / page).read_text(), page


def test_the_page_says_how_a_row_got_there_without_a_dash_in_it():
    text = " ".join(PAGE.split())
    assert "heard on his X broadcast" in text and "not advice" in text
    assert "link to the second on YouTube" in text and '"Watch on " + on + " at "' in PAGE
    assert "a person" not in text
    assert "—" not in PAGE and "–" not in PAGE


def test_the_image_ships_the_board():
    assert "COPY data/threadguy_calls.json ./data/threadguy_calls.json" in \
        (ROOT / "Dockerfile").read_text()


def test_a_row_is_dated_by_when_he_said_it_in_new_york():
    # 21 Jan 2026, 8:50pm UTC is the same evening in New York
    when = tg.said_at(dict(CHECKED, on_air="2026-01-21T20:50:08Z", x_second=3260))
    assert when.astimezone(tg.HOME).date().isoformat() == "2026-01-21"


def test_quotes_on_the_board_carry_no_dashes():
    assert tg.undash("I like \u2013 look at the IBB") == "I like, look at the IBB"
    assert tg.undash("between 750 \u2013 850") == "between 750-850"
    assert tg.undash("\u2014 sold it") == "sold it"
    board = json.loads((ROOT / "data" / "threadguy_calls.json").read_text())["calls"]
    assert not [r["id"] for r in board if re.search("[\u2013\u2014]", r["quote"])]

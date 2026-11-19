"""Levels named on air: what is accepted, when one counts as reached, and
the post that says so."""

from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import calls, levels  # noqa: E402

# Market Bubble Episode 24, 29 October 2026, around 1:35:00.
LINES = [
    {"t": 5705.0, "text": "I've been, I've been thinking about what the fuck do I buy if this shit really does nuke"},
    {"t": 5708.0, "text": "Cause I'm thinking right now I'm in the camp."},
    {"t": 5710.0, "text": "The ETH goes like 1800."},
    {"t": 5712.0, "text": "Bitcoin goes to like 65."},
    {"t": 5716.0, "text": "I'm in that camp right now."},
    {"t": 5720.0, "text": "I think I want to buy Zcash at like 750, bro."},
    {"t": 5730.0, "text": "I think anywhere between 750 and 900 is probably pretty good value."},
    {"t": 5745.0, "text": "I thought it was going to hold like 1400 and it broke down, I'm hedged short the majors at 2k."},
    {"t": 5760.0, "text": "Zcash is going to a 100 billion market cap at 6,000 a coin bro trust me."},
]
EPISODE = {"episode_id": "x-2115904746355851479", "title": "Market Bubble Episode 24",
           "segments": LINES}
VOICES = {"0": "Ansem", "1": "Ansem", "4": "Ansem", "5": "Ansem", "7": "Ansem", "8": "Ansem"}
CLAIM = {"line": 5, "speaker": "Ansem", "asset": "Zcash", "symbol": "zec",
         "asset_class": "crypto", "kind": "buy", "level": 750,
         "quote": "I think I want to buy Zcash at like 750, bro"}


# --- the words, the voice, the number ---------------------------------------------

def test_a_real_level_is_accepted_and_quoted_from_the_transcript():
    found, why = levels.accept(CLAIM, EPISODE, VOICES)
    assert why == "" and found is not None
    assert found.id == "x-2115904746355851479:5:ZEC:750"
    assert found.quote == "I think I want to buy Zcash at like 750, bro"
    assert (found.symbol, found.kind, found.level, found.speaker) == ("ZEC", "buy", 750.0, "Ansem")
    assert found.start_seconds == 5720.0


def test_a_line_nobody_labelled_is_nobodys_level():
    claim = dict(CLAIM, line=3, symbol="BTC", kind="target", level=65000,
                 quote="Bitcoin goes to like 65. I'm in that camp right now.")
    assert levels.accept(claim, EPISODE, VOICES) == (None, "voice not known")


def test_starting_on_his_line_carries_the_unlabelled_ones_after_it():
    claim = dict(CLAIM, line=1, symbol="ETH", kind="target", level=1800,
                 quote="Cause I'm thinking right now I'm in the camp. The ETH goes like 1800.")
    found, why = levels.accept(claim, EPISODE, VOICES)
    assert why == "" and found.level == 1800.0 and found.line == 1


def test_the_number_has_to_be_in_the_quote():
    claim = dict(CLAIM, level=700)
    assert levels.accept(claim, EPISODE, VOICES) == (None, "the number is not in the quote")


def test_a_market_cap_is_not_a_price():
    claim = dict(CLAIM, line=8, kind="target", level=6000,
                 quote="Zcash is going to a 100 billion market cap at 6,000 a coin bro trust me")
    assert levels.accept(claim, EPISODE, VOICES) == (None, "a market cap is not a price")


def test_a_hedge_is_not_a_level():
    claim = dict(CLAIM, line=7, symbol="ETH", kind="line", level=2000,
                 quote="it broke down, I'm hedged short the majors at 2k")
    assert levels.accept(claim, EPISODE, VOICES) == (None, "a hedge is not a call")


def test_a_quote_the_model_wrote_itself_is_refused():
    claim = dict(CLAIM, quote="I want to buy Zcash at 750 when it nukes bro")
    assert levels.accept(claim, EPISODE, VOICES) == (None, "quote is not in the transcript")


def test_the_voice_decides_who_said_it_and_not_the_model():
    claim = dict(CLAIM, speaker="FaZe Banks")
    assert levels.accept(claim, EPISODE, VOICES) == (None, "voice is not who the model named")


@pytest.mark.parametrize("change, why", [
    ({"line": "x"}, "no line"), ({"kind": "moon"}, "no kind"), ({"level": "soon"}, "no level"),
    ({"level": 0}, "no level"), ({"symbol": ""}, "no symbol"),
])
def test_a_claim_missing_a_part_is_refused_with_the_reason(change, why):
    assert levels.accept(dict(CLAIM, **change), EPISODE, VOICES) == (None, why)


@pytest.mark.parametrize("text, wanted", [
    ("Bitcoin's at 78K", [78000.0]), ("hold like 1400", [1400.0]),
    ("like 1,400 bucks", [1400.0]), ("79.4 for BTC", [79.4]), ("$750", [750.0]),
    ("1.8k for ETH", [1800.0]), ("2 million", [2e6]), ("the 2nd time", []),
    ("between 750 and 900", [750.0, 900.0]),
])
def test_numbers_are_read_with_their_thousands(text, wanted):
    assert levels.numbers_in(text) == wanted


@pytest.mark.parametrize("level, quote, said", [
    (65000, "Bitcoin goes to like 65", True), (750, "buy Zcash at like 750", True),
    (1800, "ETH 1.8 is the line", True), (900, "buy Zcash at like 750", False),
    (6500, "Bitcoin goes to like 65", False), (0, "at 0", False),
])
def test_a_level_may_drop_its_thousands_and_nothing_else(level, quote, said):
    assert levels.said_in(level, quote) is said


def test_saying_the_same_number_twice_in_a_show_is_one_level():
    first, _ = levels.accept(CLAIM, EPISODE, VOICES)
    again = levels.Level(**{**first.as_row(), "id": "x:9:ZEC:750", "line": 9})
    other = levels.Level(**{**first.as_row(), "id": "x:9:ZEC:900", "line": 9, "level": 900.0})
    kept = levels.one_per_show([again, first, other])
    assert [k.line for k in kept] == [5, 9] and kept[0] is first


# --- what the market did ---------------------------------------------------------

SAID = datetime(2026, 10, 29, 22, 8, tzinfo=UTC)


def hourly(start: datetime, rows: list[tuple[float, float, float]]) -> list[levels.Bar]:
    return [(start + timedelta(hours=i), h, lo, c) for i, (h, lo, c) in enumerate(rows)]


def test_the_price_then_is_the_last_hour_that_had_finished():
    bars = hourly(datetime(2026, 10, 29, 20, tzinfo=UTC),
                  [(1270, 1240, 1249), (1265, 1250, 1260), (1262, 1230, 1241)])
    assert levels.price_at(bars, SAID) == 1260


def test_no_price_when_the_source_has_a_hole_that_evening():
    bars = hourly(datetime(2026, 10, 29, 10, tzinfo=UTC), [(1, 1, 1260)])
    assert levels.price_at(bars, SAID) is None


def test_which_way_a_level_points_comes_from_the_price_and_not_the_model():
    assert levels.side(750, 1260) == "below" and levels.side(2000, 1260) == "above"


def test_a_level_far_from_the_price_is_a_misread():
    assert levels.sane(750, 1260) and not levels.sane(30, 62000) and not levels.sane(750, None)


def test_a_level_below_is_reached_on_a_low_after_he_spoke():
    bars = hourly(datetime(2026, 10, 29, 22, tzinfo=UTC), [
        (1262, 740, 1241),          # the hour he spoke in: does not count
        (1250, 1200, 1210), (1100, 749, 800), (820, 700, 780)])
    check = levels.status(750, 1260, SAID, bars)
    assert check["state"] == "hit" and check["side"] == "below"
    assert check["hit_at"] == "2026-10-30T00:00+00:00"
    assert check["best"] == 700 and check["price_now"] == 780 and check["away_pct"] is None


def test_a_level_above_is_reached_on_a_high():
    bars = hourly(datetime(2026, 10, 29, 23, tzinfo=UTC), [(1300, 1250, 1290), (1410, 1300, 1380)])
    check = levels.status(1400, 1260, SAID, bars)
    assert check["state"] == "hit" and check["side"] == "above"
    assert check["hit_at"] == "2026-10-30T00:00+00:00"


def test_an_open_level_says_how_far_is_left():
    bars = hourly(datetime(2026, 10, 29, 23, tzinfo=UTC), [(1260, 900, 910), (920, 905, 908)])
    check = levels.status(750, 1260, SAID, bars)
    assert check["state"] == "open" and check["best"] == 900
    assert check["away_pct"] == pytest.approx((908 - 750) / 908 * 100, abs=0.01)
    assert not levels.is_near(check)


def test_a_fresh_price_past_the_level_counts_before_the_next_bar():
    bars = hourly(datetime(2026, 10, 29, 23, tzinfo=UTC), [(1260, 900, 910)])
    now_at = datetime(2026, 11, 19, 3, tzinfo=UTC)
    check = levels.status(750, 1260, SAID, bars, now=748, now_at=now_at)
    assert check["state"] == "hit" and check["hit_at"] == "2026-11-19T03:00+00:00"


def test_within_five_percent_is_near_and_reached_is_not():
    assert levels.is_near({"state": "open", "away_pct": 4.9})
    assert not levels.is_near({"state": "open", "away_pct": 5.1})
    assert not levels.is_near({"state": "hit", "away_pct": None})
    assert not levels.is_near({"state": "open", "away_pct": None})


@pytest.mark.parametrize("junk", [None, {}, {"chart": {"result": []}}, "nope"])
def test_an_error_body_is_no_bars(junk):
    assert levels.read_bars(junk) == []


def test_a_chart_reply_is_read_with_its_highs_and_lows():
    reply = {"chart": {"result": [{"timestamp": [1761775200, 1761778800],
                                   "indicators": {"quote": [{"high": [10, None],
                                                             "low": [8, 7.5],
                                                             "close": [9, "x"]}]}}]}}
    bars = levels.read_bars(reply)
    assert [b[1:] for b in bars] == [(10.0, 8.0, 9.0), (None, 7.5, None)]


# --- alerts, once each -------------------------------------------------------------

def row(ident: str, **check) -> dict:
    return {"id": ident, "symbol": "ZEC", "speaker": "Ansem", "kind": "buy", "level": 750.0,
            "date": "2026-10-29", "price_then": 1260.0,
            "quote": "I think I want to buy Zcash at like 750, bro",
            "link": "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=5720",
            "check": {"side": "below", "price_now": 908.0, "away_pct": None,
                      "hit_at": None, "state": "open", **check}}


LAST_LOOK = datetime(2026, 11, 18, 4, tzinfo=UTC)


def test_reached_since_the_last_look_is_an_alert():
    found = levels.alerts([row("a", state="hit", hit_at="2026-11-18T09:00+00:00")], {}, LAST_LOOK)
    assert [(f["id"], f["alert"]) for f in found] == [("a", "hit")]


def test_reached_before_the_last_look_is_history_and_not_an_alert():
    assert levels.alerts([row("a", state="hit", hit_at="2026-11-01T09:00+00:00")], {}, LAST_LOOK) == []


def test_an_alert_is_given_once():
    hit = row("a", state="hit", hit_at="2026-11-18T09:00+00:00")
    near = row("b", away_pct=3.0)
    seen = levels.remember([hit, near], {})
    assert seen == {"hit": ["a"], "near": ["b"]}
    assert levels.alerts([hit, near], seen, LAST_LOOK) == []


def test_near_is_an_alert_and_then_reached_is_another():
    near = levels.alerts([row("a", away_pct=3.0)], {}, LAST_LOOK)
    assert [f["alert"] for f in near] == ["near"]
    later = levels.alerts([row("a", state="hit", hit_at="2026-11-19T01:00+00:00")],
                          {"near": ["a"]}, LAST_LOOK)
    assert [f["alert"] for f in later] == ["hit"]


def test_a_quiet_level_never_alerts():
    """A support he expected to hold: losing it only says he was wrong."""
    quiet = dict(row("a", state="hit", hit_at="2026-11-18T09:00+00:00"), quiet=True)
    near = dict(row("b", away_pct=2.0), quiet=True)
    assert levels.alerts([quiet, near], {}, LAST_LOOK) == []
    assert levels.remember([quiet, near], {}) == {"hit": ["a"], "near": ["b"]}


def test_a_dated_target_counts_only_until_its_date():
    late = dict(row("a", state="hit", hit_at="2027-01-02T09:00+00:00"), by="2026-12-31")
    in_time = dict(row("b", state="hit", hit_at="2026-12-30T09:00+00:00"), by="2026-12-31")
    since = datetime(2026, 12, 1, tzinfo=UTC)
    assert [f["id"] for f in levels.alerts([late, in_time], {}, since)] == ["b"]
    near_late = dict(row("c", away_pct=2.0), by="2026-12-31")
    assert levels.alerts([near_late], {}, since, now=datetime(2027, 1, 5, tzinfo=UTC)) == []


def test_the_same_number_said_on_three_shows_is_one_alert_from_the_first():
    first = dict(row("june", state="hit", hit_at="2026-11-18T09:00+00:00"), said_at="2026-06-25T20:06:38+00:00")
    again = dict(row("july", state="hit", hit_at="2026-11-18T09:00+00:00"), said_at="2026-07-02T23:38:28+00:00")
    other = dict(row("other", state="hit", hit_at="2026-11-18T09:00+00:00"), level=900.0,
                 said_at="2026-07-02T23:38:28+00:00")
    found = levels.alerts([again, first, other], {}, LAST_LOOK)
    assert [f["id"] for f in found] == ["june", "other"]
    assert levels.remember([again, first], {})["hit"] == ["july", "june"]


# --- the post ----------------------------------------------------------------------

def test_the_draft_is_the_accounts_voice_with_one_link():
    hit = row("a", state="hit", hit_at="2026-11-19T01:00+00:00", price_now=742.0)
    text = levels.draft(hit, "hit")
    assert text.split("\n\n") == [
        "zec just hit ansem's buy at $750.",
        "market bubble, october 29, 2026, with zec at $1,260:",
        '"i think i want to buy zcash at like 750, bro"',
        "zec is $742 now.",
        "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=5720",
    ]
    assert text.count("http") == 1 and "—" not in text and "–" not in text


def test_a_near_draft_says_how_far():
    assert levels.headline(row("a", away_pct=3.5), "near") == "zec is 3.5% from ansem's $750 buy."


@pytest.mark.parametrize("kind, side, first", [
    ("buy", "above", "btc just broke $100,000, where banks said he'd buy."),
    ("sell", "above", "btc just hit banks's sell at $100,000."),
    ("target", "above", "btc just hit banks's $100,000 target."),
    ("target", "below", "btc just fell to $100,000, where banks said it was going."),
    ("line", "below", "btc just lost $100,000, the line banks drew."),
    ("line", "above", "btc just took back $100,000, the line banks drew."),
])
def test_every_kind_and_side_has_a_headline(kind, side, first):
    item = dict(row("a"), symbol="BTC", speaker="FaZe Banks", kind=kind, level=100000.0)
    item["check"] = dict(item["check"], side=side)
    assert levels.headline(item, "hit") == first


@pytest.mark.parametrize("value, shown", [
    (62778.03, "$62,778"), (907.58, "$908"), (23.456, "$23.46"), (0.012345, "$0.01235"), (None, "?"),
])
def test_prices_are_written_the_way_people_say_them(value, shown):
    assert levels.money(value) == shown


def test_quotes_lose_their_curly_marks_and_dashes():
    assert levels.plain("It’s “over” — Bro") == "it's \"over\", bro"


# --- what ships --------------------------------------------------------------------

DATA = ROOT / "data"
SHIPPED = DATA / "levels.json"
needs_levels = pytest.mark.skipif(not SHIPPED.exists(), reason="no levels built yet")


def transcripts() -> dict[str, dict]:
    plain = DATA / "episodes.json"
    rows = (json.loads(plain.read_text()) if plain.exists()
            else json.loads(gzip.decompress((DATA / "episodes.json.gz").read_bytes())))
    found: dict[str, dict] = {}
    for item in rows:
        found.setdefault(str(item["episode_id"]), item)
    return found


@needs_levels
def test_every_level_is_said_word_for_word_on_its_line_in_that_voice():
    shows, voices = transcripts(), json.loads((DATA / "speaker_map.json").read_text())
    for item in json.loads(SHIPPED.read_text())["levels"]:
        episode = shows[item["episode_id"]]
        span = calls.locate(item["quote"], episode["segments"], item["line"])
        assert span is not None and span.first == item["line"], item["id"]
        assert calls.cut(episode["segments"], span) == item["quote"], item["id"]
        assert calls.voice(voices[item["episode_id"]], span.first, span.last) == item["speaker"], item["id"]
        assert levels.said_in(item["level"], item["quote"]), item["id"]
        assert levels.sane(item["level"], item["price_then"]), item["id"]
        assert item["link"].endswith(f"t={int(item['start_seconds'])}"), item["id"]


@needs_levels
def test_every_level_is_one_a_person_kept_and_every_candidate_was_read():
    verdicts = json.loads((DATA / "levels_verdicts.json").read_text())
    kept = {ident for ident, verdict in verdicts.items() if verdict.get("keep")}
    assert {item["id"] for item in json.loads(SHIPPED.read_text())["levels"]} == kept
    found = json.loads((DATA / "levels_candidates.json").read_text())
    unread = [c["id"] for show in found["shows"].values() for c in show["candidates"]
              if c["id"] not in verdicts]
    assert not unread, unread[:5]

"""A call on the board is something a host said, in his voice, word for word.

The model that proposes calls is not trusted with any of that. These are
the checks that stand between its proposals and the page.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import calls  # noqa: E402

# 36:15 to 37:39 of the 8 October show, as transcribed.
LINES = [
    (2175.0, "weekly candle is sunday 8 p.m east so like the next sunday 8 p.m east so for people who don't"),
    (2181.0, "um typically like bracket things that way so i think next week what i will be looking for the most"),
    (2187.0, "my highest conviction spot i'll give a few actually i think um want to go um i think i think something"),
    (2195.0, "is kind of like under discussed actually right now is starkware um starkware is an interesting one"),
    (2203.0, "because right now people are super concerned with the quantum stuff um and if they were to break like"),
    (2254.0, "i can tell you i'm gonna be honest with you i i've never i've never i've never heard of it in my"),
    (2259.0, "fucking life what yeah it's not like you would only you would only it's strk you would only know"),
    (2265.0, "what starkware is if you like were one of the super technical people in crypto i've been following"),
    (2318.0, "very long pump i have a lot of pump um i think like they are going to continue to dominate the"),
]
EPISODE = {"episode_id": "x-2108360500283335093", "title": "BULL RUN IS OVER?",
           "published_at": "2026-10-09",
           "segments": [{"t": t, "text": text} for t, text in LINES]}
# Lines 5 and 6 are Banks, and nobody labelled them.
VOICES = {"0": "Ansem", "1": "Ansem", "2": "Ansem", "3": "Ansem", "4": "Ansem",
          "7": "Ansem", "8": "Ansem"}

CLAIM = {"line": 2, "speaker": "Ansem", "asset": "Starknet", "symbol": "strk",
         "asset_class": "crypto", "stance": "long", "kind": "pick", "target": "",
         "quote": "my highest conviction spot i'll give a few actually i think um want to go um i think "
                  "i think something is kind of like under discussed actually right now is starkware"}


def test_a_real_call_is_accepted_and_quoted_from_the_transcript():
    call, why = calls.accept(CLAIM, EPISODE, VOICES)
    assert why == "" and call is not None
    assert (call.id, call.line, call.last_line) == ("x-2108360500283335093:2:STRK", 2, 3)
    assert call.start_seconds == 2187.0
    assert (call.speaker, call.symbol, call.stance, call.kind) == ("Ansem", "STRK", "long", "pick")
    assert call.quote == CLAIM["quote"]


def test_what_is_shown_is_the_transcripts_spelling_and_not_the_models():
    """A show transcribed with capitals and full stops, and a model that
    writes the same words its own way."""
    show = {"episode_id": "e", "segments": [
        {"t": 60.0, "text": "over-invested in crypto. I own a fuck ton of Bitcoin. I own a fuck ton"},
        {"t": 66.0, "text": "of Hyperliquid, and I own, you know, a seven-figure CryptoPunk portfolio."}]}
    claim = dict(CLAIM, line=0, speaker="FaZe Banks", symbol="HYPE", asset="Hyperliquid", kind="holding",
                 quote="i own a fuck ton of bitcoin i own a fuck ton of hyperliquid")
    call, why = calls.accept(claim, show, {"0": "FaZe Banks", "1": "FaZe Banks"})
    assert why == ""
    assert call.quote == "I own a fuck ton of Bitcoin. I own a fuck ton of Hyperliquid"
    assert (call.line, call.last_line, call.start_seconds) == (0, 1, 60.0)


def test_two_assets_named_on_one_line_are_two_calls_with_two_ids():
    show = {"episode_id": "e", "segments": [
        {"t": 60.0, "text": "I own a fuck ton of Bitcoin. I own a fuck ton of Hyperliquid, and that is it."}]}
    voices = {"0": "FaZe Banks"}
    base = dict(CLAIM, line=0, speaker="FaZe Banks", kind="holding",
                quote="I own a fuck ton of Bitcoin. I own a fuck ton of Hyperliquid")
    btc, _ = calls.accept(dict(base, symbol="BTC", asset="Bitcoin"), show, voices)
    hype, _ = calls.accept(dict(base, symbol="HYPE", asset="Hyperliquid"), show, voices)
    punks, _ = calls.accept(dict(base, symbol="", asset="Crypto Punks"), show, voices)
    assert {btc.id, hype.id, punks.id} == {"e:0:BTC", "e:0:HYPE", "e:0:crypto-punks"}
    assert len(calls.one_per_show([btc, hype, punks])) == 3


def test_punctuation_and_capitals_do_not_matter_but_every_word_does():
    dressed = dict(CLAIM, quote="My highest conviction spot, I'll give a few actually. I think, um, "
                                "want to go, um, I think I think something is kind of like under discussed")
    assert calls.accept(dressed, EPISODE, VOICES)[0] is not None
    # The same sentence with the "um" taken out is not what was said.
    tidied = dict(CLAIM, quote="my highest conviction spot i'll give a few actually i think want to go "
                               "i think something is kind of like under discussed actually right now is starkware")
    assert calls.accept(tidied, EPISODE, VOICES) == (None, "quote is not in the transcript")


def test_a_quote_the_model_wrote_itself_is_refused():
    invented = dict(CLAIM, quote="starkware is my highest conviction trade for next week and i am buying it")
    assert calls.accept(invented, EPISODE, VOICES) == (None, "quote is not in the transcript")


def test_the_model_may_miscount_the_line_by_a_little_and_no_more():
    assert calls.accept(dict(CLAIM, line=0), EPISODE, VOICES)[0].line == 2
    assert calls.accept(dict(CLAIM, line=5), EPISODE, VOICES)[0].line == 2
    far = dict(CLAIM, line=8)
    assert calls.accept(far, EPISODE, VOICES) == (None, "quote is not in the transcript")
    assert calls.accept(dict(CLAIM, line=99), EPISODE, VOICES)[0] is None
    assert calls.accept(dict(CLAIM, line="soon"), EPISODE, VOICES) == (None, "no line")


def test_a_line_nobody_labelled_is_nobodys_call():
    """Banks says he has never heard of it. No label, so no row, however
    obvious it is who is talking."""
    banks = dict(CLAIM, line=5, speaker="FaZe Banks", stance="short",
                 quote="i can tell you i'm gonna be honest with you i i've never i've never i've never heard of it")
    assert calls.accept(banks, EPISODE, VOICES) == (None, "voice not known")


def test_a_sentence_two_people_said_is_nobodys_call():
    voices = dict(VOICES, **{"3": "FaZe Banks"})
    assert calls.accept(CLAIM, EPISODE, voices) == (None, "voice not known")


def test_the_voice_decides_who_said_it_and_not_the_model():
    wrong = dict(CLAIM, speaker="FaZe Banks")
    assert calls.accept(wrong, EPISODE, VOICES) == (None, "voice is not who the model named")
    # A guest's name on a host's line is refused the same way.
    assert calls.accept(dict(CLAIM, speaker="Mert"), EPISODE, VOICES)[0] is None


@pytest.mark.parametrize("change,why", [
    ({"stance": "bullish"}, "no stance"),
    ({"stance": None}, "no stance"),
    ({"kind": "vibes"}, "no kind"),
    ({"symbol": "", "asset": "  "}, "no asset"),
    ({"quote": "is starkware"}, "quote is not in the transcript"),
    ({"quote": ""}, "quote is not in the transcript"),
])
def test_a_claim_missing_a_part_is_refused_with_the_reason(change, why):
    assert calls.accept(dict(CLAIM, **change), EPISODE, VOICES) == (None, why)


def test_a_quote_said_twice_is_placed_where_the_model_pointed():
    twice = {"episode_id": "e", "segments": [
        {"t": 10.0, "text": "i am still very long pump and i have a lot"},
        {"t": 20.0, "text": "something else entirely was said in between here"},
        {"t": 30.0, "text": "i am still very long pump and i have a lot"}]}
    voices = {"0": "Ansem", "1": "Ansem", "2": "Ansem"}
    claim = dict(CLAIM, quote="i am still very long pump and i have a lot", symbol="PUMP")
    assert calls.accept(dict(claim, line=2), twice, voices)[0].start_seconds == 30.0
    assert calls.accept(dict(claim, line=0), twice, voices)[0].start_seconds == 10.0


def test_a_symbol_is_cleaned_before_it_is_kept():
    call, _ = calls.accept(dict(CLAIM, symbol=" $strk! "), EPISODE, VOICES)
    assert call.symbol == "STRK"


# --- once per show ------------------------------------------------------------

def a_call(line, kind="holding", symbol="PUMP", speaker="Ansem", stance="long", episode="e1"):
    return calls.Call(id=f"{episode}:{line}", episode_id=episode, line=line, last_line=line,
                      start_seconds=float(line), speaker=speaker, asset=symbol.title(),
                      symbol=symbol, asset_class="crypto", stance=stance, kind=kind,
                      quote="q", target="")


def test_saying_it_four_times_is_one_call():
    kept = calls.one_per_show([a_call(40), a_call(10), a_call(90), a_call(70)])
    assert [c.line for c in kept] == [10]


def test_the_stronger_statement_is_the_one_kept():
    """He says he holds it, and two minutes later puts a number on it."""
    kept = calls.one_per_show([a_call(10), a_call(50, kind="target"), a_call(80, kind="pick")])
    assert [(c.line, c.kind) for c in kept] == [(50, "target")]


def test_two_hosts_two_assets_two_shows_and_two_sides_are_all_separate():
    found = [a_call(1), a_call(2, speaker="FaZe Banks"), a_call(3, symbol="STRK"),
             a_call(4, episode="e2"), a_call(5, stance="short"), a_call(6, kind="exit")]
    assert len(calls.one_per_show(found)) == 6


# --- when it was said ---------------------------------------------------------

def test_a_broadcast_knows_the_minute_it_went_live():
    when = calls.aired("x-2108360500283335093")
    assert when == datetime(2026, 10, 9, 0, 54, 49, 368000, tzinfo=UTC)


def test_an_upload_has_its_date_and_the_hour_the_show_airs():
    assert calls.aired("oqX0jt1C598", "2026-10-09") == datetime(2026, 10, 9, 20, tzinfo=UTC)
    assert calls.aired("oqX0jt1C598", "") is None
    assert calls.aired("x-nonsense", "soon") is None


@pytest.mark.parametrize("hour,minute,day", [
    (0, 54, 9),      # just after midnight: priced at the mark it just passed
    (11, 59, 9),
    (12, 0, 10),     # from noon on, the next one is nearer
    (20, 0, 10),     # the usual hour: four hours to the next mark
    (23, 59, 10),
])
def test_a_show_is_priced_at_the_nearest_midnight_utc(hour, minute, day):
    when = datetime(2026, 10, 9, hour, minute, tzinfo=UTC)
    assert calls.daily_mark(when) == date(2026, 10, day)


# --- what it did after --------------------------------------------------------

def test_a_long_is_up_when_the_price_is_and_a_short_when_it_is_not():
    assert calls.moved("long", 0.10, 0.125) == 25.0
    assert calls.moved("long", 0.10, 0.05) == -50.0
    assert calls.moved("short", 100.0, 80.0) == 20.0
    assert calls.moved("short", 100.0, 150.0) == -50.0


@pytest.mark.parametrize("then,now", [(None, 1.0), (1.0, None), (0, 1.0), (1.0, 0),
                                      (-1.0, 2.0), (2.0, -1.0)])
def test_a_missing_or_impossible_price_gives_no_number(then, now):
    assert calls.moved("long", then, now) is None


def test_the_summary_counts_only_rows_with_a_number_and_uses_the_middle_one():
    rows = [{"moved_pct": 1900.0}, {"moved_pct": 10.0}, {"moved_pct": -20.0},
            {"moved_pct": -40.0}, {"moved_pct": None}, {}]
    told = calls.tally(rows)
    assert (told["calls"], told["priced"], told["up"], told["down"]) == (6, 4, 2, 2)
    # Not 462.5, which is what one coin going up twentyfold does to a mean.
    assert told["median_pct"] == -5.0
    assert (told["best_pct"], told["worst_pct"]) == (1900.0, -40.0)
    assert calls.tally([{"moved_pct": 7.0}, {"moved_pct": 1.0}, {"moved_pct": 3.0}])["median_pct"] == 3.0


def test_a_board_with_nothing_priced_says_so():
    told = calls.tally([{}, {"moved_pct": None}])
    assert told == {"calls": 2, "priced": 0, "up": 0, "down": 0,
                    "median_pct": None, "best_pct": None, "worst_pct": None}


# --- prices -------------------------------------------------------------------

from app import call_prices  # noqa: E402


def stamp(y, m, d, hour=0, minute=0):
    return int(datetime(y, m, d, hour, minute, tzinfo=UTC).timestamp())


# The shape the price source really answers in, cut down: bitcoin over the
# 8 October show, with the day it had no bar for.
BTC = {"chart": {"result": [{
    "meta": {"symbol": "BTC-USD", "longName": "Bitcoin USD", "shortName": "Bitcoin USD",
             "instrumentType": "CRYPTOCURRENCY", "regularMarketPrice": 83504.65},
    "timestamp": [stamp(2026, 10, 9), stamp(2026, 10, 10), stamp(2026, 10, 11), stamp(2026, 10, 12)],
    "indicators": {"quote": [{"open": [81676.33, 82546.32, None, 83673.0],
                              "close": [82546.32, 82912.22, None, 83504.64]}]}}]}}
# A stock: one bar per session, stamped at the New York open.
NVDA = {"chart": {"result": [{
    "meta": {"symbol": "NVDA", "longName": "NVIDIA Corporation", "shortName": "NVIDIA Corporation"},
    "timestamp": [stamp(2026, 10, 7, 13, 30), stamp(2026, 10, 8, 13, 30), stamp(2026, 10, 9, 13, 30)],
    "indicators": {"quote": [{"open": [236.0, 238.1, 231.0],
                              "close": [237.47, 230.48, 229.28]}]}}]}}


def test_a_chart_reply_is_read_into_its_listing_and_its_days():
    meta, bars = call_prices.read_chart(BTC)
    assert meta["symbol"] == "BTC-USD" and len(bars) == 4
    assert bars[0] == (datetime(2026, 10, 9, tzinfo=UTC), 81676.33, 82546.32)
    assert bars[2][1:] == (None, None)


@pytest.mark.parametrize("junk", [
    None, {}, [], "429", {"chart": {"result": None, "error": {"code": "Not Found"}}},
    {"chart": {"result": []}}, {"chart": {"result": [{"meta": {}}]}},
])
def test_an_error_body_is_no_listing_and_no_days(junk):
    assert call_prices.read_chart(junk) == ({}, [])


def test_a_bad_bar_is_a_missing_price_and_not_a_zero():
    reply = {"chart": {"result": [{"meta": {}, "timestamp": [stamp(2026, 10, 9), "soon", stamp(2026, 10, 10)],
                                   "indicators": {"quote": [{"open": [0, 5, -3], "close": ["9", True]}]}}]}}
    _, bars = call_prices.read_chart(reply)
    assert [(o, c) for _, o, c in bars] == [(None, None), (None, None)]


def test_a_token_is_priced_at_midnight_utc_on_the_mark():
    _, bars = call_prices.read_chart(BTC)
    assert call_prices.at_mark(bars, date(2026, 10, 9)) == 81676.33
    assert call_prices.at_mark(bars, date(2026, 10, 10)) == 82546.32
    # No bar that day: the close of the day before is the same moment.
    assert call_prices.at_mark(bars, date(2026, 10, 11)) == 82912.22
    # The day before had none either, and nothing is borrowed from further off.
    bare = [b for b in bars if b[0].day in (9, 12)]
    assert call_prices.at_mark(bare, date(2026, 10, 11)) is None
    assert call_prices.at_mark([], date(2026, 10, 9)) is None


def test_a_stock_is_priced_at_the_close_as_the_show_went_live():
    _, bars = call_prices.read_chart(NVDA)
    # Thursday 8 October, 1pm Pacific: the bell has just rung.
    assert call_prices.close_by(bars, datetime(2026, 10, 8, 20, 0, tzinfo=UTC)) == 230.48
    # The broadcast that started 00:54 UTC on the 9th is still Thursday's close.
    assert call_prices.close_by(bars, datetime(2026, 10, 9, 0, 54, tzinfo=UTC)) == 230.48
    # Over a weekend it is Friday's.
    assert call_prices.close_by(bars, datetime(2026, 10, 12, 9, 0, tzinfo=UTC)) == 229.28
    # Before the first bar, or long after the last, there is no honest price.
    assert call_prices.close_by(bars, datetime(2026, 10, 7, 9, 0, tzinfo=UTC)) is None
    assert call_prices.close_by(bars, datetime(2026, 10, 20, 20, 0, tzinfo=UTC)) is None


def test_the_latest_price_skips_a_day_with_no_bar():
    _, bars = call_prices.read_chart(BTC)
    assert call_prices.latest(bars) == (83504.64, datetime(2026, 10, 12, tzinfo=UTC))
    assert call_prices.latest(bars[:3]) == (82912.22, datetime(2026, 10, 10, tzinfo=UTC))
    assert call_prices.latest([]) is None


def test_a_reply_has_to_be_for_the_listing_that_was_asked_for():
    meta, _ = call_prices.read_chart(BTC)
    assert call_prices.is_listing(meta, "BTC-USD", "Bitcoin")
    assert call_prices.is_listing(meta, "btc-usd", "bitcoin")
    # Another coin answering to the ticker, or the right symbol with
    # somebody else's name on it.
    assert not call_prices.is_listing(meta, "BTC-USD", "Bitcoin Cash")
    assert not call_prices.is_listing(dict(meta, symbol="BTC1-USD"), "BTC-USD", "Bitcoin")
    assert not call_prices.is_listing({}, "BTC-USD", "Bitcoin")
    assert not call_prices.is_listing(meta, "BTC-USD", "")


def test_fresh_prices_are_read_for_whatever_answered():
    reply = {"NVDA": {"symbol": "NVDA", "close": [229.28]},
             "btc-usd": {"symbol": "BTC-USD", "close": [None, 83504.65, None]},
             "DEAD-USD": {"symbol": "DEAD-USD", "close": [None]},
             "ZERO-USD": {"close": [0]}, "ODD": "not a row", "EMPTY": {}}
    assert call_prices.read_spark(reply) == {"NVDA": 229.28, "BTC-USD": 83504.65}
    assert call_prices.read_spark(None) == {} and call_prices.read_spark([]) == {}


class Source:
    """The price source: answers some batches and fails others."""

    def __init__(self, fail_on=()):
        self.asked, self.fail_on = [], fail_on

    async def get(self, url, headers=None, params=None):
        self.asked.append(params["symbols"].split(","))
        if len(self.asked) in self.fail_on:
            raise TimeoutError("down")
        rows = {s: {"close": [1.0 + len(s)]} for s in self.asked[-1]}

        class Reply:
            def raise_for_status(self):
                return None

            def json(self):
                return rows
        return Reply()


@pytest.mark.anyio
async def test_fresh_prices_are_asked_for_in_batches_and_a_dead_batch_costs_only_itself():
    wanted = [f"S{i:02}-USD" for i in range(45)] + ["s00-usd", ""]
    source = Source(fail_on=(2,))
    found = await call_prices.now(wanted, client=source)
    assert [len(batch) for batch in source.asked] == [20, 20, 5]
    assert len(found) == 25 and "S00-USD" in found and "S20-USD" not in found


# --- the board as it is served --------------------------------------------------

SHIPPED = {"built_at": "2026-10-12T04:00:00+00:00", "priced_at": "2026-10-11", "calls": [
    {"id": "e1:10", "episode_id": "e1", "show": "BULL RUN IS OVER?", "aired": "2026-10-09T00:54:49+00:00",
     "date": "2026-10-08", "speaker": "Ansem", "asset": "Starknet", "symbol": "STRK", "class": "crypto",
     "listing": "STRK22691-USD", "stance": "long", "kind": "pick", "target": "", "quote": "q",
     "start_seconds": 2187.0, "timestamp": "36:27", "link": "https://x.com/i/broadcasts/1XxygwAplMyGM?t=2187",
     "price_then": 0.10, "then_at": "2026-10-09", "price_last": 0.12, "last_at": "2026-10-11"},
    {"id": "e1:20", "episode_id": "e1", "show": "BULL RUN IS OVER?", "aired": "2026-10-09T00:54:49+00:00",
     "date": "2026-10-08", "speaker": "FaZe Banks", "asset": "Ethereum", "symbol": "ETH", "class": "crypto",
     "listing": "ETH-USD", "stance": "short", "kind": "holding", "target": "", "quote": "q",
     "start_seconds": 900.0, "timestamp": "15:00", "link": "https://x.com/i/broadcasts/1XxygwAplMyGM?t=900",
     "price_then": 2000.0, "then_at": "2026-10-09", "price_last": 2200.0, "last_at": "2026-10-11"},
    {"id": "e2:5", "episode_id": "e2", "show": "Older", "aired": "2026-07-02T20:00:00+00:00",
     "date": "2026-07-02", "speaker": "Ansem", "asset": "Nvidia", "symbol": "NVDA", "class": "stock",
     "listing": "NVDA", "stance": "long", "kind": "exit", "target": "", "quote": "q",
     "start_seconds": 60.0, "timestamp": "1:00", "link": "https://x.com/i/broadcasts/abc?t=60",
     "price_then": 200.0, "then_at": "2026-07-02", "price_last": 229.28, "last_at": "2026-10-09"},
]}


def test_the_board_is_whole_with_no_fresh_prices_and_says_when_its_are_from():
    served = calls.board(SHIPPED)
    assert [r["moved_pct"] for r in served["calls"]] == [20.0, -10.0, None]
    assert [r["now_at"] for r in served["calls"]] == ["2026-10-11", "2026-10-11", "2026-10-09"]
    assert served["live"] is False and served["priced_at"] == "2026-10-11"
    assert served["shows"] == 2
    assert served["summary"]["priced"] == 2 and served["summary"]["median_pct"] == 5.0
    assert served["by_speaker"]["Ansem"]["up"] == 1
    assert served["by_speaker"]["FaZe Banks"]["down"] == 1


def test_fresh_prices_are_laid_over_the_rows_they_cover():
    served = calls.board(SHIPPED, {"strk22691-usd": 0.15}, "2026-10-12")
    strk, eth, _ = served["calls"]
    assert (strk["price_now"], strk["now_at"], strk["moved_pct"]) == (0.15, "2026-10-12", 50.0)
    assert (eth["price_now"], eth["now_at"]) == (2200.0, "2026-10-11")
    assert served["live"] is True and served["priced_at"] == "2026-10-12"


def test_a_short_is_scored_the_other_way_round():
    served = calls.board(SHIPPED, {"ETH-USD": 1500.0}, "2026-10-12")
    assert served["calls"][1]["moved_pct"] == 25.0


def test_an_exit_is_shown_and_never_scored():
    served = calls.board(SHIPPED, {"NVDA": 260.0}, "2026-10-12")
    assert served["calls"][2]["price_now"] == 260.0
    assert served["calls"][2]["moved_pct"] is None
    assert served["summary"]["priced"] == 2


@pytest.mark.parametrize("wild", [0.12 * 25, 0.12 / 25, 0, -1.0, None])
def test_a_fresh_price_that_cannot_be_the_same_asset_is_ignored(wild):
    """A symbol reused for another coin, or a bad tick. The row keeps the
    price it was built with."""
    served = calls.board(SHIPPED, {"STRK22691-USD": wild}, "2026-10-12")
    strk = served["calls"][0]
    assert (strk["price_now"], strk["now_at"], strk["moved_pct"]) == (0.12, "2026-10-11", 20.0)
    assert served["live"] is False


def test_how_a_price_was_looked_up_stays_on_the_server():
    served = calls.board(SHIPPED)
    assert all("listing" not in r and "price_last" not in r for r in served["calls"])


def test_an_empty_file_is_an_empty_board_and_not_an_error():
    served = calls.board({})
    assert served["calls"] == [] and served["summary"]["priced"] == 0
    assert served["live"] is False


# --- the board that ships -------------------------------------------------------
#
# Everything above checks the machinery. These hold the file the site
# serves against the transcripts it claims to quote.

import gzip  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

DATA = ROOT / "data"


def shipped_board() -> list[dict]:
    return json.loads((DATA / "calls.json").read_text())["calls"]


def transcripts() -> dict[str, dict]:
    plain = DATA / "episodes.json"
    rows = (json.loads(plain.read_text()) if plain.exists()
            else json.loads(gzip.decompress((DATA / "episodes.json.gz").read_bytes())))
    found: dict[str, dict] = {}
    for row in rows:
        found.setdefault(str(row["episode_id"]), row)
    return found


def test_every_row_is_said_word_for_word_at_that_second_in_that_voice():
    shows, voices = transcripts(), json.loads((DATA / "speaker_map.json").read_text())
    board = [row for row in shipped_board() if row["source"] == "show"]
    assert len(board) >= 50
    for row in board:
        episode = shows[row["episode_id"]]
        line = int(row["id"].split(":")[-2]) if ":hand:" in row["id"] else None
        at = next(i for i, s in enumerate(episode["segments"])
                  if float(s["t"]) == float(row["start_seconds"])
                  and (line is None or abs(i - line) <= calls.SLACK))
        span = calls.locate(row["quote"], episode["segments"], at)
        assert span is not None and span.first == at, row["id"]
        assert calls.cut(episode["segments"], span) == row["quote"], row["id"]
        assert calls.voice(voices[row["episode_id"]], span.first, span.last) == row["speaker"], row["id"]


def test_every_row_is_one_a_person_kept_and_nothing_kept_is_missing():
    verdicts = json.loads((DATA / "calls_verdicts.json").read_text())
    kept = {ident for ident, verdict in verdicts.items() if verdict.get("keep")}
    assert {row["id"] for row in shipped_board()} == kept
    assert len(kept) == len(shipped_board())


def test_every_candidate_has_been_read():
    """A new show adds candidates. Until somebody has ruled on each one,
    this fails, and the board cannot quietly fall behind the archive."""
    verdicts = json.loads((DATA / "calls_verdicts.json").read_text())
    found = json.loads((DATA / "calls_candidates.json").read_text())
    proposed = [c for show in found["shows"].values() for c in show["candidates"]]
    proposed += (found.get("posts") or {}).get("candidates", [])
    unread = [c["id"] for c in proposed if c["id"] not in verdicts]
    assert not unread, unread[:5]


def test_every_call_from_a_post_is_in_that_post_word_for_word():
    """The posts are not in the repo. The few a row quotes are, so this
    can hold each row against the post it says it came from."""
    posts_on_file = json.loads((DATA / "calls_posts.json").read_text())
    rows = [row for row in shipped_board() if row["source"] == "post"]
    assert rows
    for row in rows:
        number = row["id"].split(":")[1]
        post = posts_on_file[number]
        text = " ".join(post["text"].split())
        assert calls.locate_in_text(row["quote"], text) is not None, row["id"]
        assert row["quote"] in text, row["id"]
        assert row["link"] == f"https://x.com/blknoiz06/status/{number}", row["id"]
        assert row["speaker"] == "Ansem" and row["episode_id"] == "", row["id"]
        assert datetime.fromisoformat(row["aired"]) == datetime.fromisoformat(
            post["created_at"].replace("Z", "+00:00")), row["id"]
        assert post["kind"] != "reply", row["id"]


def test_a_call_is_not_on_the_board_twice_because_he_said_it_and_posted_it():
    """He read his Robinhood post out on the next day's show. One row."""
    board = shipped_board()
    said = {(r["speaker"], r["listing"], r["stance"], r["date"]) for r in board
            if r["source"] == "show"}
    doubled = [r["id"] for r in board if r["source"] == "post"
               and (r["speaker"], r["listing"], r["stance"], r["date"]) in said]
    assert not doubled, doubled


def test_every_row_is_priced_from_a_listing_on_file_and_a_host_said_it():
    listings = json.loads((DATA / "call_assets.json").read_text())
    on_file = {a["yahoo"]: a for a in listings.values()}
    for row in shipped_board():
        assert row["speaker"] in calls.HOSTS, row["id"]
        assert row["stance"] in calls.STANCES and row["kind"] in calls.KINDS, row["id"]
        assert row["listing"] in on_file, row["id"]
        assert row["asset"] == on_file[row["listing"]]["name"], row["id"]
        assert row["price_then"] > 0 and row["price_last"] > 0, row["id"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["then_at"]), row["id"]
        # Priced within two days of going live, never from another week.
        aired = datetime.fromisoformat(row["aired"])
        assert abs((date.fromisoformat(row["then_at"]) - aired.date()).days) <= 2, row["id"]


def test_every_row_plays_on_x_at_its_second():
    players = set(json.loads((DATA / "broadcast_links.json").read_text()).values())
    for row in shipped_board():
        if row["source"] == "post":
            assert row["link"].startswith("https://x.com/blknoiz06/status/"), row["id"]
            continue
        base, _, second = row["link"].partition("?t=")
        assert second == str(int(row["start_seconds"])), row["id"]
        # Every show but the first has a player that opens at the second.
        assert base in players or base.startswith("https://x.com/MarketBubble/status/"), row["id"]


def test_a_host_is_on_the_board_once_per_asset_per_show_per_side():
    seen = [(r["episode_id"] or r["aired"][:10], r["speaker"], r["listing"], r["stance"])
            for r in shipped_board()]
    assert len(seen) == len(set(seen))
    assert len({r["id"] for r in shipped_board()}) == len(seen)


def test_the_misses_are_on_the_board():
    """A board of only the good ones would be an advert."""
    served = calls.board(json.loads((DATA / "calls.json").read_text()))
    assert served["summary"]["down"] >= 5
    assert served["summary"]["worst_pct"] < 0


# --- a trade is not a call ------------------------------------------------------
#
# The first board said Ansem was short Solana and down 64%. What he said was
# that he had hedged his spot bags, and twelve minutes later that he was
# bullish on Solana long term. Lex read the row and said it was not true.

HEDGE = {"episode_id": "x-2062633295792271762", "segments": [
    {"t": 1285.0, "text": "Like I have been bearish, Bitcoin, Ethan, Sol."},
    {"t": 1287.0, "text": "Like I think it's a lot difficult to manage shorts, but I've been like head short those"},
    {"t": 1293.0, "text": "in addition to the spot bags that I hold."}]}


def test_a_hedge_is_refused_however_clearly_he_says_he_is_short():
    claim = dict(CLAIM, line=0, symbol="SOL", asset="Solana", stance="short", kind="holding",
                 quote="Like I have been bearish, Bitcoin, Ethan, Sol. Like I think it's a lot difficult to "
                       "manage shorts, but I've been like head short those in addition to the spot bags that I hold.")
    voices = {"0": "Ansem", "1": "Ansem", "2": "Ansem"}
    assert calls.accept(claim, HEDGE, voices) == (None, "a hedge is not a call")


@pytest.mark.parametrize("said,why", [
    ("but I've been like head short those in addition to the spot bags", "a hedge is not a call"),
    ("i'm hedged short the majors against my spot", "a hedge is not a call"),
    ("this is just a hedge on the alts i hold", "a hedge is not a call"),
    ("I was looking at like short term trade on this", "a short-term trade is not a call"),
    ("Stop beneath like 62 and I'll be targeting like all time highs at 77",
     "a setup with a stop is a trade, not a call"),
    ("i think it's gonna go to 190 but i had my stop there", "a setup with a stop is a trade, not a call"),
])
def test_the_words_that_mark_a_trade(said, why):
    assert calls.tactical(said) == why


@pytest.mark.parametrize("said", [
    "my highest conviction spot right now is starkware",
    "I own a fuck ton of Bitcoin. I own a fuck ton of Hyperliquid",
    "I'm expecting Solana to go to 150 during Q3",
    "people need to stop sleeping on this, i think pump looks really good",
    "the head of the fed said so and i'm still long",
    "",
])
def test_a_view_is_not_mistaken_for_a_trade(said):
    assert calls.tactical(said) is None


def test_no_trade_is_on_the_board():
    for row in shipped_board():
        assert calls.tactical(row["quote"]) is None, row["id"]


def test_a_short_is_not_built_without_a_reason_on_file():
    """Nearly every short said on air is a hedge or a trade. The build
    stops at one unless whoever kept it wrote down why it is neither."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_calls

    row = {"id": "e:1:ETH", "episode_id": "e", "line": 1, "last_line": 1, "start_seconds": 1.0,
           "speaker": "Ansem", "asset": "Ethereum", "symbol": "ETH", "asset_class": "crypto",
           "stance": "short", "kind": "holding", "quote": "q", "target": ""}
    found = {"shows": {"e": {"candidates": [row]}}}
    with pytest.raises(SystemExit, match="a short needs"):
        build_calls.kept_calls(found, {"e:1:ETH": {"keep": True}})
    with pytest.raises(SystemExit, match="a short needs"):
        build_calls.kept_calls(found, {"e:1:ETH": {"keep": True, "why": "  "}})
    kept = build_calls.kept_calls(found, {"e:1:ETH": {
        "keep": True, "why": "Says the cycle top is in for it and that he is short it until it halves."}})
    assert [r["id"] for r in kept] == ["e:1:ETH"]
    # A long needs no such note.
    assert build_calls.kept_calls({"shows": {"e": {"candidates": [dict(row, stance="long")]}}},
                                  {"e:1:ETH": {"keep": True}})


def test_what_came_off_the_first_board_has_its_reason_on_file():
    verdicts = json.loads((DATA / "calls_verdicts.json").read_text())
    came_off = [v for v in verdicts.values() if "first board" in str(v.get("why") or "")]
    assert len(came_off) == 6 and not any(v["keep"] for v in came_off)
    assert not [r for r in shipped_board() if r["stance"] == "short"]


def test_a_correction_reaches_a_tab_that_was_already_open():
    """Rows came off the board for being wrong, and a tab opened before
    that went on showing 81 calls with them in it."""
    assert 'fetch(API + "/v1/calls", {cache: "no-store"})' in PAGE
    assert 'document.addEventListener("visibilitychange"' in PAGE
    assert "load(false)" in PAGE
    # The refresh does not scroll the reader back to a linked row, and a
    # failed one does not blank a board that is on screen.
    assert "if (!quiet) lit();" in PAGE and "if (!first) return;" in PAGE


def test_the_finder_is_gentle_with_the_proxy_it_shares_by_default():
    source = (ROOT / "scripts" / "extract_calls.py").read_text()
    assert 'os.environ.get("CALLS_CONCURRENCY", "2")' in source


def test_the_board_can_be_found():
    """It was live for two days with no way to reach it but a pasted link."""
    assert '<meta name="robots" content="noindex">' not in PAGE
    assert '<a class="also-card" href="/calls">' in (ROOT / "demo" / "podcast.html").read_text()
    assert "https://search.lexthedev.com/calls :" in (ROOT / "demo" / "llms.txt").read_text()


def test_the_page_shows_a_post_as_a_post_and_a_target_as_checked():
    assert "Read the post" in PAGE and "<span class='tag'>in a post</span>" in PAGE
    assert 'said.replace(/^said he /, "wrote that he ")' in PAGE
    body = PAGE.split("function checked(c){")[1].split("function row(r, i){")[0]
    for words in (' reached"', ' not reached by "', ' not reached yet"'):
        assert words in body, words
    assert "esc(text)" in body
    assert "priced at the hour he posted it" in PAGE
    assert "and the calls Ansem made in" in PAGE


def test_the_page_says_trades_are_left_off():
    assert "Hedges and short-term" in PAGE and "whichever way they went" in PAGE


# --- a target with a number in it ---------------------------------------------
#
# "Solana to 150 in Q3" sat beside a green +43% and nothing said the
# quarter had ended with a high of 125.

Q3 = [(date(2026, 7, 20), 96.0), (date(2026, 8, 28), 110.0), (date(2026, 9, 27), 124.62),
      (date(2026, 10, 2), 123.48)]


def test_a_dated_target_that_was_not_touched_in_time_is_missed():
    found = calls.target_status(150, "2026-09-30", Q3, date(2026, 10, 14))
    assert found == {"level": 150.0, "by": "2026-09-30", "high": 124.62,
                     "status": "missed", "on": None}


def test_a_target_is_reached_on_the_first_day_the_price_touched_it():
    found = calls.target_status(110, "2026-09-30", Q3, date(2026, 10, 14))
    assert (found["status"], found["on"]) == ("reached", "2026-08-28")


def test_touching_it_after_the_date_he_gave_does_not_count():
    late = Q3 + [(date(2026, 10, 9), 151.0)]
    assert calls.target_status(150, "2026-09-30", late, date(2026, 10, 14))["status"] == "missed"
    # With no date, the same touch reaches it.
    assert calls.target_status(150, None, late, date(2026, 10, 14))["on"] == "2026-10-09"


def test_a_target_is_open_until_its_date_passes_or_it_is_reached():
    assert calls.target_status(150, "2026-09-30", Q3[:2], date(2026, 9, 1))["status"] == "open"
    assert calls.target_status(150, "2026-09-30", Q3[:3], date(2026, 9, 30))["status"] == "open"
    assert calls.target_status(600, None, Q3, date(2030, 1, 1))["status"] == "open"
    assert calls.target_status(150, "not a date", Q3, date(2030, 1, 1))["status"] == "open"
    assert calls.target_status(150, "2026-09-30", [], date(2026, 9, 1))["high"] is None


@pytest.mark.parametrize("level", [None, 0, -5, "150", True])
def test_a_target_with_no_price_in_it_is_not_checked(level):
    """ "Ten percent of Bitcoin's market cap" is not a price. """
    assert calls.target_status(level, "2026-09-30", Q3, date(2026, 10, 14)) is None


def test_daily_highs_are_read_and_a_day_with_none_is_left_out():
    reply = {"chart": {"result": [{"meta": {}, "timestamp": [
        stamp(2026, 9, 26), stamp(2026, 9, 27), stamp(2026, 9, 28), "soon"],
        "indicators": {"quote": [{"high": [120.1, 124.62, None, 9.0]}]}}]}}
    assert call_prices.read_highs(reply) == [(date(2026, 9, 26), 120.1), (date(2026, 9, 27), 124.62)]
    assert call_prices.read_highs({"chart": {"result": None}}) == []
    assert call_prices.read_highs(None) == []


def test_every_dated_target_on_the_board_whose_date_has_passed_says_how_it_ended():
    built = date.fromisoformat(json.loads((DATA / "calls.json").read_text())["built_at"][:10])
    checked = [r for r in shipped_board() if r.get("target_check")]
    assert len(checked) >= 6
    for row in checked:
        check = row["target_check"]
        assert check["status"] in ("reached", "missed", "open"), row["id"]
        if check["by"] and date.fromisoformat(check["by"]) < built:
            assert check["status"] != "open", row["id"]
        if check["status"] == "missed":
            assert check["high"] < check["level"], row["id"]
    # The three that started this.
    q3 = [r for r in checked if r["symbol"] == "SOL" and r["target_check"]["by"] == "2026-09-30"]
    assert len(q3) == 3 and all(r["target_check"]["status"] == "missed" for r in q3)
    assert not [r for r in shipped_board() if "not reached" in r["target"]]


def test_a_target_touched_since_the_board_was_built_is_said_at_once():
    shipped = {"calls": [dict(SHIPPED["calls"][0], kind="target", target="15 cents",
                              target_check={"level": 0.14, "by": None, "high": 0.12,
                                            "status": "open", "on": None})]}
    still = calls.board(shipped, {"STRK22691-USD": 0.13}, "2026-10-14")["calls"][0]
    assert still["target_check"]["status"] == "open"
    there = calls.board(shipped, {"STRK22691-USD": 0.15}, "2026-10-14")["calls"][0]
    assert there["target_check"] == {"level": 0.14, "by": None, "high": 0.15,
                                     "status": "reached", "on": "2026-10-14"}
    # No fresh price, no claim.
    assert calls.board(shipped)["calls"][0]["target_check"]["status"] == "open"


# --- a call he wrote ------------------------------------------------------------

POST = {"id": "2078942029116264788", "created_at": "2026-07-19T20:36:19.000Z", "kind": "quote",
        "text": "bought $PUMP here on the reclaim of old support @ .001675\n\n"
                "thesis: making 30-40M a month during bear market for onchain"}
POSTED = {"post": "2078942029116264788", "asset": "Pump.fun", "symbol": "pump",
          "asset_class": "crypto", "stance": "long", "kind": "holding", "target": "",
          "quote": "bought $PUMP here on the reclaim of old support @ .001675"}


def test_a_call_from_a_post_is_cut_from_the_post_and_keyed_by_it():
    row, why = calls.accept_post(POSTED, POST, "Ansem")
    assert why == ""
    assert row["id"] == "post:2078942029116264788:PUMP" and row["source"] == "post"
    assert row["quote"] == "bought $PUMP here on the reclaim of old support @ .001675"
    assert (row["speaker"], row["posted_at"]) == ("Ansem", "2026-07-19T20:36:19.000Z")


def test_the_signs_around_a_number_are_kept():
    post = dict(POST, text="$SOL from $60 to $600 this cycle, up 900%\nSPX500 is going to end the year @ 8,000+ easy")
    row, _ = calls.accept_post(dict(POSTED, symbol="SOL", quote="SOL from $60 to $600 this cycle, up 900"),
                               post, "Ansem")
    assert row["quote"] == "$SOL from $60 to $600 this cycle, up 900%"
    row, _ = calls.accept_post(dict(POSTED, symbol="SPX", quote="spx500 is going to end the year @ 8,000"),
                               post, "Ansem")
    assert row["quote"] == "SPX500 is going to end the year @ 8,000+"


@pytest.mark.parametrize("change,why", [
    ({"quote": "bought pump here because the chart looks incredible to me"}, "quote is not in the post"),
    ({"quote": "bought $PUMP"}, "quote is not in the post"),
    ({"stance": "bullish"}, "no stance"),
    ({"kind": "vibes"}, "no kind"),
    ({"symbol": "", "asset": ""}, "no asset"),
])
def test_a_claim_about_a_post_is_refused_with_the_reason(change, why):
    assert calls.accept_post(dict(POSTED, **change), POST, "Ansem") == (None, why)


def test_a_hedge_or_a_trade_in_a_post_is_refused_too():
    post = dict(POST, text="shorting $ETH here as a hedge against my spot bags for the week")
    claim = dict(POSTED, symbol="ETH", stance="short",
                 quote="shorting $ETH here as a hedge against my spot bags")
    assert calls.accept_post(claim, post, "Ansem") == (None, "a hedge is not a call")


def test_one_asset_one_side_once_a_day_and_the_strongest_wins():
    def posted(ident, at, kind, symbol="PUMP", stance="long"):
        return {"id": ident, "posted_at": at, "kind": kind, "symbol": symbol,
                "asset": symbol, "stance": stance}
    rows = [posted("a", "2026-08-20T10:00:00Z", "holding"),
            posted("b", "2026-08-20T21:00:00Z", "pick"),
            posted("c", "2026-08-20T22:00:00Z", "pick"),
            posted("d", "2026-08-21T09:00:00Z", "holding"),
            posted("e", "2026-08-20T11:00:00Z", "holding", symbol="ZEC"),
            posted("f", "2026-08-20T12:00:00Z", "holding", stance="short")]
    assert [r["id"] for r in calls.one_per_day(rows)] == ["e", "f", "b", "d"]


# --- the route and the page -----------------------------------------------------

def test_the_route_serves_the_board_and_survives_the_price_source_being_down(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    async def down(listings, *, client):
        raise TimeoutError("price source is down")
    monkeypatch.setattr(main.call_prices, "now", down)
    monkeypatch.setattr(main, "_CALL_PRICES", (0.0, {}, None))
    with TestClient(main.app) as client:
        reply = client.get("/v1/calls")
        assert reply.status_code == 200
        assert reply.headers["cache-control"] == "no-store"
        body = reply.json()
        assert len(body["calls"]) == len(shipped_board())
        assert body["live"] is False and body["priced_at"]
        assert all(r["price_now"] and "listing" not in r for r in body["calls"])
        moved = client.get("/calls", follow_redirects=False)
        assert moved.status_code == 301 and moved.headers["location"] == "/demo/calls.html"
        assert client.get("/demo/calls.html").status_code == 200


def test_fresh_prices_are_fetched_once_and_reused(monkeypatch):
    import asyncio

    import app.main as main

    asked = []

    async def answers(listings, *, client):
        asked.append(sorted(set(listings)))
        return {"BTC-USD": 90000.0}
    monkeypatch.setattr(main.call_prices, "now", answers)
    monkeypatch.setattr(main, "_CALL_PRICES", (0.0, {}, None))

    async def twice():
        first = await main._call_prices_now(["BTC-USD", "ETH-USD"])
        second = await main._call_prices_now(["BTC-USD", "ETH-USD"])
        return first, second
    first, second = asyncio.run(twice())
    assert len(asked) == 1 and first == second
    assert first[0] == {"BTC-USD": 90000.0} and re.fullmatch(r"\d{4}-\d{2}-\d{2}", first[1])


def test_a_board_that_is_not_there_is_a_503_and_not_a_crash(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setattr(main, "_CALLS_FILE", tmp_path / "nothing.json")
    with TestClient(main.app) as client:
        assert client.get("/v1/calls").status_code == 503
    broken = tmp_path / "calls.json"
    broken.write_text("{not json")
    monkeypatch.setattr(main, "_CALLS_FILE", broken)
    monkeypatch.setattr(main, "_CALLS_READ", (0.0, {}))
    assert main._calls_file() == {}


PAGE = (ROOT / "demo" / "calls.html").read_text()


def test_the_page_carries_no_figure_that_can_go_stale():
    """Every count and price comes from /v1/calls."""
    visible = re.sub(r"<(script|style)\b.*?</\1>", "", PAGE, flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", visible)
    assert not re.search(r"\b\d{2,}\s*(calls|shows|%)", visible)
    assert 'fetch(API + "/v1/calls"' in PAGE


def test_the_page_says_what_the_number_is_and_is_not():
    assert "not advice" in PAGE
    assert "says nothing" in PAGE and "what he bought, how much, or when he sold" in PAGE
    assert "The misses are here too." in PAGE


def test_nothing_from_the_data_reaches_the_page_unescaped():
    """Quotes are speech-to-text and titles are somebody else's."""
    body = PAGE.split("function row(r, i){")[1].split("function render(){")[0]
    words = "speaker|symbol|asset|quote|show|timestamp|target"
    for field in words.split("|"):
        assert f"esc(r.{field})" in body, field
    # Never joined into the markup bare.
    assert not re.search(rf"\+\s*r\.(?:{words})\b", body)
    assert "safeUrl(r.link)" in body and "esc(href)" in body
    assert "javascript:" not in body


def test_the_page_has_no_em_dashes_and_fits_a_phone():
    assert "—" not in PAGE
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in PAGE
    assert "grid-template-columns:minmax(0,1fr)" in PAGE
    assert "overflow-wrap:anywhere" in PAGE

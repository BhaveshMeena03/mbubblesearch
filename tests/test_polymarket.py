"""The Polymarket board: reading the ticker, matching it, and serving it.

Every OCR line and API response in here is real: the lines were read off
the 8 Oct, 22 Oct, 29 Oct and 5 Nov 2026 broadcasts, and the markets and
price histories under tests/fixtures/polymarket are Polymarket's own
answers, recorded from a GitHub runner on 10 Nov 2026.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import polymarket as pm

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "polymarket"
MARKETS = {m["id"]: m for m in json.loads((FIX / "markets.json").read_text())}
BTC80_HISTORY = json.loads((FIX / "btc80_history.json").read_text())
ASTRA_HISTORY = json.loads((FIX / "astra_history.json").read_text())
# When the 8 Oct (US) and 5 Nov shows went out, from their replays' playlists.
OCT9 = datetime(2026, 10, 9, 0, 54, 50, tzinfo=UTC)
NOV5 = datetime(2026, 11, 5, 21, 36, 46, tzinfo=UTC)


def at(start, seconds):
    return start + timedelta(seconds=seconds)


# --- reading the ticker -----------------------------------------------------------

def test_a_line_gives_every_market_whose_odds_are_on_it():
    got = pm.readings("¢ Will Ethereum dip to $2,200 in November? YES 87% Bb Will "
                      "Bitcoin reach $82,500 in November? YES 40%")
    assert [(r.text, r.yes, r.whole) for r in got] == [
        ("Will Ethereum dip to $2,200 in November?", 87, True),
        ("Will Bitcoin reach $82,500 in November?", 40, True),
    ]


def test_a_market_cut_off_at_the_left_edge_is_marked_partial():
    got = pm.readings("lovember 15, 2026? YES 13% (—) Will the Kansas 2026 Senate race "
                      "be within5%? YES 14% e Will Ethereur")
    assert got[0].whole is False and got[0].yes == 13
    assert got[1] == pm.Reading("Will the Kansas 2026 Senate race be within5%?", 14, True)
    # What trails after the last odds is still coming in from the right.
    assert len(got) == 2


def test_the_icon_between_markets_is_not_part_of_the_question():
    got = pm.readings(") Willthe next GPT Terra model (5.7+) debut at a score of at least "
                      "1440 by June 30, 2027? YES 26% 2) Will the Bank of E")
    assert got[0].text.startswith("Willthe next GPT Terra") and got[0].whole


def test_a_question_word_inside_a_cut_off_sentence_is_not_a_start():
    got = pm.readings("ast nominee can be confirmed by the Senate? YES 40%")
    assert got and got[0].whole is False


def test_odds_without_a_question_mark_before_them_are_not_taken():
    assert pm.readings("Will Bitcoin reach $82,500 in November‘ YES 40%") == []


def test_figures_are_compared_as_figures():
    assert pm.numbers("Will Bitcoin dip to $80,000 in October?") == ["80000"]
    # A comma before four digits is a missing space, not a thousands mark.
    assert pm.numbers("solved by December 31,2028?") == ["2028", "31"]
    assert pm.numbers("Will XRP dip to $1.20 by December 31, 2026?") == ["1.2", "2026", "31"]


# --- is it the same question ------------------------------------------------------

@pytest.mark.parametrize("reading, question", [
    ("Bb Will Bitcoin reach $82,500 in November?", "Will Bitcoin reach $82,500 in November?"),
    ("Will XAP dip to $1.20 by December 31,2026?", "Will XRP dip to $1.20 by December 31, 2026?"),
    ("Will Jason or Lucia die in GTAVI?", "Will Jason or Lucia die in GTA VI?"),
    ("Will the Kansas 2026 Senate race be within5%?", "Will the Kansas 2026 Senate race be within 5%?"),
])
def test_ocr_slips_are_forgiven(reading, question):
    assert pm.same_question(reading, question, whole=True)[0]


@pytest.mark.parametrize("reading, question, why", [
    # Polymarket's sister markets differ by one figure, one month or one word.
    ("Will Bitcoin dip to $80,000 in October?", "Will Bitcoin dip to $82,500 in October?",
     "figures differ"),
    ("Will Bitcoin dip to $80,000 in October?", "Will Bitcoin dip to $80,000 in November?",
     "months differ"),
    ("Will Bitcoin reach $82,500 in November?", "Will Bitcoin dip to $82,500 in November?",
     "words differ"),
    # 93% alike letter for letter, same date: only the name differs.
    ("Will Apple be the largest company in the world by market cap on November 30, 2026?",
     "Will NVIDIA be the largest company in the world by market cap on November 30, 2026?",
     "a word differs"),
])
def test_a_sister_market_is_never_taken_for_the_one_on_screen(reading, question, why):
    ok, reason = pm.same_question(reading, question, whole=True)
    assert not ok and reason.startswith(why)


def test_a_partial_reading_matches_the_end_of_its_question():
    ok, _ = pm.same_question("stra 6.1+ be released between November 9 and November 15, 2026?",
                             "Will GPT Astra 6.1+ be released between November 9 and "
                             "November 15, 2026?", whole=False)
    assert ok
    ok, why = pm.same_question("stra 6.1+ be released between November 9 and November 15, 2026?",
                               "Will GPT Astra 6.1+ be released between November 16 and "
                               "November 22, 2026?", whole=False)
    assert not ok


def test_a_whole_reading_shorter_than_the_question_is_not_it():
    ok, why = pm.same_question("Will Bitcoin reach $82,500?",
                               "Will Bitcoin reach $82,500 in November?", whole=True)
    assert not ok


# --- grouping sightings -----------------------------------------------------------

def sighting(t, text, yes, whole=True):
    return pm.Sighting(t=t, aired=at(NOV5, t), yes=yes, text=text, whole=whole)


def test_a_short_question_seen_whole_is_grouped_however_it_was_spaced():
    got = pm.group([sighting(10, "Netanyahu out by June 30, 2027?", 57),
                    sighting(230, "Netanyahu out by June 30,2027?", 58),
                    sighting(450, "Netanyahu out by June 30, 2027?", 57)])
    assert len(got) == 1 and len(got[0].sightings) == 3
    assert got[0].text == "Netanyahu out by June 30, 2027?"


def test_two_companies_with_the_same_question_are_two_markets():
    tail = " be the largest company in the world by market cap on November 30, 2026?"
    got = pm.group([sighting(10, "Will Apple" + tail, 79), sighting(30, "Will NVIDIA" + tail, 25),
                    sighting(250, "Will Apple" + tail, 78),
                    # Cut off where the names were: it fits both, so it is dropped.
                    sighting(260, "rgest company in the world by market cap on November 30, 2026?",
                             25, whole=False)])
    assert sorted(len(g.sightings) for g in got) == [1, 2]
    assert {g.text.split()[1] for g in got} == {"Apple", "NVIDIA"}


def test_a_partial_reading_joins_the_one_market_it_fits():
    got = pm.group([sighting(10, "Will GPT Astra 6.1+ be released between November 9 and "
                                 "November 15, 2026?", 68),
                    sighting(220, "be released between November 9 and November 15, 2026?", 68,
                             whole=False)])
    assert len(got) == 1 and len(got[0].sightings) == 2


# --- the time ---------------------------------------------------------------------

PLAYLIST = """#EXTM3U
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-PROGRAM-DATE-TIME:2026-11-05T21:36:46.151Z
#EXTINF:2.002,
chunk_0_b.ts
#EXT-X-PROGRAM-DATE-TIME:2026-11-05T21:36:47.660Z
#EXTINF:2.002,
chunk_1_b.ts
#EXTINF:2.002,
chunk_2_b.ts
"""


def test_each_segment_knows_where_it_is_in_the_replay_and_when_it_aired():
    segs = pm.playlist(PLAYLIST)
    assert [s.uri for s in segs] == ["chunk_0_b.ts", "chunk_1_b.ts", "chunk_2_b.ts"]
    assert [s.start for s in segs] == [0.0, 2.002, 4.004]
    assert segs[1].aired == datetime(2026, 11, 5, 21, 36, 47, 660000, tzinfo=UTC)
    # Undated: the one before it, plus its length.
    assert segs[2].aired == segs[1].aired + timedelta(seconds=2.002)
    assert pm.sample(segs, 2) == [segs[0], segs[2]]


def test_a_replay_that_does_not_say_when_it_aired_is_refused():
    with pytest.raises(ValueError):
        pm.playlist("#EXTM3U\n#EXTINF:2.002,\nchunk_0_b.ts\n")


@pytest.mark.parametrize("text, want", [
    ("11/@5/26\n@5: 36 PM EST", (36, "EST")),
    ("11/05/26 4: 56 PM EST", (56, "EST")),
    ("1@/@3/26 1@:@1 PM EDT", (1, "EDT")),
    ("VU -9.25'% iy AD A ood,", None),
    ("11/05/26 4: 76 PM EST", None),
])
def test_the_corner_clock_gives_its_minutes_and_zone(text, want):
    assert pm.read_clock(text) == want


def test_the_clock_is_held_to_the_playlist_and_its_zone():
    aired = datetime(2026, 11, 5, 22, 36, 40, tzinfo=UTC)          # 5:36 pm EST
    assert pm.clock_offset(aired, (36, "EST")) == 0
    assert pm.clock_offset(aired, (37, "EST")) == 1
    assert pm.clock_offset(aired, (16, "EST")) == -20
    # EDT on a November evening is the wrong clock, not a small slip.
    assert pm.clock_offset(aired, (36, "EDT")) is None


# What the clock really read on 5 Nov 2026 against the playlist: right where
# the digits were read right, scattered wherever a digit was misread.
REAL_OFFSETS = [0, 0, -20, 0, 4, 0, 0, -3, 0, 0, -20, -23, 0, 4, 0, 0, -3, 0, 4, -21, -4, 1, -1]


def test_a_replay_dated_right_passes_despite_the_misread_digits():
    assert pm.clock_holds(REAL_OFFSETS)


def test_a_replay_dated_a_few_minutes_out_fails():
    assert not pm.clock_holds([o + 5 if abs(o) <= 1 else o for o in REAL_OFFSETS])
    assert not pm.clock_holds([o + 2 if abs(o) <= 1 else o for o in REAL_OFFSETS])


def test_too_few_readings_or_the_wrong_zone_fails():
    assert not pm.clock_holds([0] * 9)
    assert not pm.clock_holds([0] * 10 + [None] * 4)


# --- the price --------------------------------------------------------------------

def test_the_ticker_showed_polymarkets_own_price():
    # 5 Nov, an hour in: "Will GPT Astra 6.1+ be released between November 9
    # and November 15, 2026? YES 68%". Polymarket had it at 67.5.
    assert pm.price_at(ASTRA_HISTORY, at(NOV5, 3600)) == 0.675
    # 8 Oct (US): "Will Bitcoin dip to $80,000 in October? YES 77%", 76.5.
    assert pm.price_at(BTC80_HISTORY, at(OCT9, 4000)) == 0.765
    assert pm.price_at(BTC80_HISTORY, at(OCT9, 4000) + timedelta(days=3)) is None


def test_agreement_counts_sightings_whose_odds_match_the_history():
    seen = [pm.Sighting(t=4000, aired=at(OCT9, 4000), yes=77, text="", whole=True),
            pm.Sighting(t=4200, aired=at(OCT9, 4200), yes=77, text="", whole=True)]
    got = pm.agreement(seen, BTC80_HISTORY)
    assert got.compared == 2 and got.holds
    wrong = [pm.Sighting(t=4000, aired=at(OCT9, 4000), yes=60, text="", whole=True)]
    assert not pm.agreement(wrong, BTC80_HISTORY).holds


def test_only_a_market_open_at_the_time_can_have_been_on_screen():
    # Six markets were all called "Will Bitcoin dip to $80,000 in October?".
    same = [m for m in MARKETS.values() if m["question"] == "Will Bitcoin dip to $80,000 in October?"]
    assert len(same) == 6
    on_oct9 = [m["id"] for m in same if pm.open_at(m, at(OCT9, 4000))]
    assert on_oct9 == ["5170738"]
    # The original resolved on 15 Oct; by 29 Oct it could not be the one shown.
    oct29 = datetime(2026, 10, 29, 21, 0, tzinfo=UTC)
    assert "5170738" not in [m["id"] for m in same if pm.open_at(m, oct29)]


def test_where_a_market_stands():
    assert pm.outcome(MARKETS["5170738"]) == {"state": "resolved", "result": "yes"}
    assert pm.outcome(MARKETS["6017456"])["state"] == "open"
    assert pm.outcome({**MARKETS["5170738"], "umaResolutionStatus": "proposed"}) == {
        "state": "closed", "result": None}
    assert pm.outcome({**MARKETS["5170738"], "outcomePrices": '["0", "1"]'})["result"] == "no"


def test_the_yes_token_is_the_one_paired_with_yes():
    m = MARKETS["5170738"]
    tokens = json.loads(m["clobTokenIds"])
    assert pm.yes_token(m) == tokens[0]
    flipped = {**m, "outcomes": '["No", "Yes"]'}
    assert pm.yes_token(flipped) == tokens[1]


# --- the matcher, with Polymarket stood in for ---------------------------------------

class FakePolymarket:
    def __init__(self, markets, histories):
        self.markets_, self.histories = markets, histories

    def search(self, text):
        return [({"slug": "ev-" + m["id"], "title": "Bitcoin in October"}, m)
                for m in self.markets_]

    def history(self, token, start, end):
        return self.histories.get(token, [])


def matcher():
    import importlib.util
    spec = importlib.util.spec_from_file_location("polymarket_match",
                                                  ROOT / "scripts" / "polymarket_match.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shown_btc80(yes):
    return {"text": "Will Bitcoin dip to $80,000 in October?",
            "sightings": [[4000.0, at(OCT9, 4000).isoformat(), yes, True,
                           "Will Bitcoin dip to $80,000 in October?"]]}


def test_of_six_markets_sharing_a_question_the_one_open_and_priced_right_is_chosen():
    same = [m for m in MARKETS.values() if m["question"] == "Will Bitcoin dip to $80,000 in October?"]
    api = FakePolymarket(same, {pm.yes_token(MARKETS["5170738"]): BTC80_HISTORY})
    row = matcher().match_one(api, shown_btc80(77), OCT9, at(OCT9, 14000))
    assert row["status"] == "matched" and row["market"]["id"] == "5170738"
    assert row["price_then"] == 76.5
    assert row["market"]["url"] == "https://polymarket.com/event/ev-5170738"


def test_odds_that_disagree_send_the_row_to_a_person():
    same = [MARKETS["5170738"]]
    api = FakePolymarket(same, {pm.yes_token(MARKETS["5170738"]): BTC80_HISTORY})
    row = matcher().match_one(api, shown_btc80(40), OCT9, at(OCT9, 14000))
    assert row["status"] == "review" and "odds" in row["why"]


def test_two_markets_that_both_fit_send_the_row_to_a_person():
    twin = {**MARKETS["5170738"], "id": "twin", "clobTokenIds": '["t1", "t2"]'}
    api = FakePolymarket([MARKETS["5170738"], twin],
                         {pm.yes_token(MARKETS["5170738"]): BTC80_HISTORY, "t1": BTC80_HISTORY})
    row = matcher().match_one(api, shown_btc80(77), OCT9, at(OCT9, 14000))
    assert row["status"] == "review" and "more than one" in row["why"]


def test_nothing_found_is_said_plainly():
    row = matcher().match_one(FakePolymarket([], {}), shown_btc80(77), OCT9, at(OCT9, 14000))
    assert row["status"] == "review" and row["why"] == "no market matched"


# --- the board ----------------------------------------------------------------------

def built_row(key, status="matched", yes=77, standing=None, mid="5170738"):
    return {"key": key, "status": status, "ocr": "secret ocr", "considered": [{"id": "x"}],
            "readings": 3,
            "first": {"t": 4000.0, "aired": at(OCT9, 4000).isoformat(), "yes": yes},
            "last": {"t": 4400.0, "aired": at(OCT9, 4400).isoformat()},
            "market": {"id": mid, "question": "Will Bitcoin dip to $80,000 in October?",
                       "url": "https://polymarket.com/event/x", "yes_token": "tok",
                       "event_title": "What price will Bitcoin hit in October?"},
            "price_then": 76.5, "standing": standing or {"state": "resolved", "result": "yes"}}


def shipped(*rows):
    return {"episodes": [{"episode_id": "x-1", "title": "BULL RUN IS OVER?",
                          "broadcast": "https://x.com/i/broadcasts/1XxygwAplMyGM",
                          "aired": OCT9.isoformat(), "rows": list(rows)}],
            "built_at": "2026-11-10T12:00:00+00:00", "checked_at": "2026-11-10T12:00:00+00:00"}


def test_nothing_ships_without_a_person_keeping_it():
    board = pm.board(shipped(built_row("x-1:5170738")), {})
    assert board["episodes"] == [] and board["tally"]["markets"] == 0
    dropped = pm.board(shipped(built_row("x-1:5170738")), {"x-1:5170738": {"verdict": "drop"}})
    assert dropped["tally"]["markets"] == 0


def test_a_row_that_did_not_match_never_ships_even_if_kept():
    board = pm.board(shipped(built_row("x-1:?0", status="review")), {"x-1:?0": {"verdict": "keep"}})
    assert board["tally"]["markets"] == 0


def test_a_kept_row_carries_what_a_reader_needs_and_nothing_else():
    board = pm.board(shipped(built_row("x-1:5170738")), {"x-1:5170738": {"verdict": "keep"}})
    row = board["episodes"][0]["rows"][0]
    assert row["question"] == "Will Bitcoin dip to $80,000 in October?"
    assert row["watch"] == "https://x.com/i/broadcasts/1XxygwAplMyGM?t=3998"
    assert row["went_as_leaned"] is True and row["leaned"] == "yes"
    assert not {"ocr", "considered", "market", "status"} & set(row)
    assert "yes_token" not in json.dumps(board)
    assert board["tally"] == {"episodes": 1, "markets": 1, "resolved": 1, "leaned": 1,
                              "went_as_leaned": 1}


def test_a_market_at_even_odds_leaned_nowhere():
    board = pm.board(shipped(built_row("x-1:5170738", yes=50)), {"x-1:5170738": {"verdict": "keep"}})
    assert board["tally"]["leaned"] == 0 and board["episodes"][0]["rows"][0]["leaned"] is None


def test_a_fresh_standing_replaces_the_one_it_was_built_with():
    kept = {"x-1:5170738": {"verdict": "keep"}}
    board = pm.board(shipped(built_row("x-1:5170738", standing={"state": "open", "yes_now": 70})),
                     kept, live={"5170738": {"state": "resolved", "result": "no"}})
    row = board["episodes"][0]["rows"][0]
    assert row["standing"] == {"state": "resolved", "result": "no"}
    assert row["went_as_leaned"] is False and board["tally"]["went_as_leaned"] == 0


# --- the route and the page ---------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main

    board, verdicts = tmp_path / "board.json", tmp_path / "verdicts.json"
    board.write_text(json.dumps(shipped(built_row("x-1:5170738"), built_row("x-1:?1", "review"))))
    verdicts.write_text(json.dumps({"x-1:5170738": {"verdict": "keep"}}))
    monkeypatch.setattr(main, "_PM_BOARD", board)
    monkeypatch.setattr(main, "_PM_VERDICTS", verdicts)
    monkeypatch.setattr(main, "_PM_STANDING", (0.0, {}, None))

    async def down(http, ids):
        raise TimeoutError("Polymarket is down")
    monkeypatch.setattr(main, "_pm_fetch_standing", down)
    with TestClient(main.app) as c:
        yield c


def test_the_route_serves_the_kept_rows_and_survives_polymarket_being_down(client):
    reply = client.get("/v1/polymarket")
    assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
    body = reply.json()
    assert body["tally"]["markets"] == 1 and body["live"] is False
    assert body["standing_checked_at"] == "2026-11-10T12:00:00+00:00"


def test_no_board_is_unavailable_not_an_error(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main
    monkeypatch.setattr(main, "_PM_BOARD", tmp_path / "missing.json")
    with TestClient(main.app) as c:
        assert c.get("/v1/polymarket").status_code == 503


def test_fresh_standings_are_fetched_once_and_reused(monkeypatch):
    import asyncio

    import app.main as main
    asked = []

    async def answers(http, ids):
        asked.append(list(ids))
        return {"5170738": {"state": "resolved", "result": "yes"}}
    monkeypatch.setattr(main, "_pm_fetch_standing", answers)
    monkeypatch.setattr(main, "_PM_STANDING", (0.0, {}, None))

    async def twice():
        return (await main._pm_standing_now(["5170738"]),
                await main._pm_standing_now(["5170738"]))
    first, second = asyncio.run(twice())
    assert len(asked) == 1 and first == second and first[1]


def test_the_page_is_there_unlinked_and_kept_out_of_search(client):
    moved = client.get("/polymarket", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/demo/polymarket.html"
    page = client.get("/demo/polymarket.html")
    assert page.status_code == 200
    assert '<meta name="robots" content="noindex, nofollow">' in page.text
    for other in (ROOT / "demo").glob("*.html"):
        if other.name != "polymarket.html":
            assert "/polymarket" not in other.read_text(encoding="utf-8"), other.name


def test_the_page_has_no_dashes_in_its_copy():
    text = (ROOT / "demo" / "polymarket.html").read_text(encoding="utf-8")
    assert "—" not in text and "–" not in text


# --- what is shipped ----------------------------------------------------------------

SHIPPED = ROOT / "data" / "polymarket_board.json"
VERDICTS = ROOT / "data" / "polymarket_verdicts.json"


def shipped_rows():
    if not SHIPPED.exists():
        return []
    board = json.loads(SHIPPED.read_text())
    verdicts = json.loads(VERDICTS.read_text()) if VERDICTS.exists() else {}
    return [(ep, r) for ep in board["episodes"] for r in ep["rows"] if pm.shippable(r, verdicts)]


@pytest.mark.parametrize("ep, row", shipped_rows() or [pytest.param(None, None, marks=pytest.mark.skip)])
def test_every_kept_row_passed_every_check(ep, row):
    assert row["status"] == "matched"
    a = row["agreement"]
    assert a["compared"] >= 1 and a["agree"] / a["compared"] >= pm.MIN_AGREEMENT
    assert a["worst"] is not None
    # The odds first shown and Polymarket's price then are the same market.
    assert row["price_then"] is not None
    assert abs(row["price_then"] - row["first"]["yes"]) <= pm.ODDS_TOLERANCE
    ok, why = pm.same_question(row["ocr"], row["market"]["question"], whole=True)
    assert ok, why
    aired = datetime.fromisoformat(row["first"]["aired"])
    assert datetime.fromisoformat(ep["aired"]) <= aired <= datetime.fromisoformat(ep["ended"])


def test_a_clock_that_piles_up_away_from_zero_fails_however_many_agree():
    # Most frames a minute either side, but more of them at +4: that is a
    # replay four minutes out with some lucky misreads, not a right one.
    assert not pm.clock_holds([0] * 4 + [1] * 2 + [4] * 3 + [-20, 7, 9])
    assert pm.clock_holds([0] * 9 + [4] * 3)


# --- the ticker's delay ---------------------------------------------------------------

def steps(start, *pairs):
    """A price history: (seconds after start, price) pairs."""
    return [{"t": int((start + timedelta(seconds=s)).timestamp()), "p": p} for s, p in pairs]


def test_odds_the_market_had_a_few_minutes_earlier_agree():
    # The ticker refreshes every few minutes: on screen at 1000s it still
    # says 60% while the market moved to 70% at 700s.
    history = steps(NOV5, (300, 0.60), (700, 0.70), (1000, 0.70))
    seen = pm.Sighting(t=1000, aired=at(NOV5, 1000), yes=60, text="", whole=True)
    assert pm.agrees(seen, history)
    got = pm.agreement([seen], history)
    assert got.agree == 1 and got.strict == 0


def test_the_price_in_force_when_the_window_opens_counts():
    # One trade at 0s at 60%, the next at 900s: 60% was the price all the
    # way through the window, though no point of it falls inside.
    history = steps(NOV5, (0, 0.60), (900, 0.70), (1000, 0.70))
    seen = pm.Sighting(t=1000, aired=at(NOV5, 1000), yes=60, text="", whole=True)
    assert pm.agrees(seen, history)


def test_odds_only_seen_later_do_not_agree():
    history = steps(NOV5, (1000, 0.60), (1400, 0.70))
    seen = pm.Sighting(t=1000, aired=at(NOV5, 1000), yes=70, text="", whole=True)
    assert not pm.agrees(seen, history)


def test_odds_older_than_the_window_do_not_agree():
    # 60% held from 0s to 300s; by the time the window opens at 400s the
    # market had been at 70% for a while.
    history = steps(NOV5, (0, 0.60), (300, 0.70), (900, 0.70), (1000, 0.70))
    seen = pm.Sighting(t=1000, aired=at(NOV5, 1000), yes=60, text="", whole=True)
    assert not pm.agrees(seen, history)


def test_between_two_markets_that_both_fit_the_one_right_at_the_minute_wins():
    start = OCT9
    exact = steps(start, *[(s, 0.77) for s in range(3000, 6001, 60)])
    # The twin only passed through 77% in the minutes before each sighting.
    lagged = steps(start, *[(s, 0.77 if s % 600 < 300 else 0.70) for s in range(3000, 6001, 60)])
    a = {**MARKETS["5170738"], "id": "exact", "clobTokenIds": '["e1", "e2"]'}
    b = {**MARKETS["5170738"], "id": "lagged", "clobTokenIds": '["l1", "l2"]'}
    shown = {"text": "Will Bitcoin dip to $80,000 in October?",
             "sightings": [[float(t), at(start, t).isoformat(), 77, True,
                            "Will Bitcoin dip to $80,000 in October?"]
                           for t in (3420, 4020, 4620, 5220)]}
    row = matcher().match_one(FakePolymarket([a, b], {"e1": exact, "l1": lagged}),
                              shown, start, at(start, 14000))
    assert row["status"] == "matched" and row["market"]["id"] == "exact"


def test_the_odds_shown_are_from_the_first_sighting_the_market_bears_out():
    # 5 Nov: the first reading of the GTA VI market said 81%; it was at 74.
    history = steps(NOV5, *[(s, 0.74) for s in range(0, 3001, 60)])
    m = {**MARKETS["6017456"], "question": "Will Jason or Lucia die in GTA VI?",
         "clobTokenIds": '["g1", "g2"]'}
    shown = {"text": "Will Jason or Lucia die in GTAVI?",
             "sightings": [[t, at(NOV5, t).isoformat(), y, True, "Will Jason or Lucia die in GTAVI?"]
                           for t, y in ((600.0, 81), (800.0, 74), (1000.0, 74), (1200.0, 74),
                                        (1400.0, 74))]}
    row = matcher().match_one(FakePolymarket([m], {"g1": history}), shown, NOV5, at(NOV5, 3000))
    assert row["status"] == "matched"
    assert row["first"]["yes"] == 74 and row["first"]["t"] == 800.0
    assert row["price_then"] == 74.0


def test_a_market_never_confirmed_at_its_own_minute_goes_to_a_person():
    # 29 Oct: one reading at 83%, the market at 70 by then; only the lag
    # window joins them, so it is not taken.
    history = steps(OCT9, (3500, 0.83), (3900, 0.70), (4000, 0.70))
    m = {**MARKETS["5170738"], "clobTokenIds": '["c1", "c2"]'}
    row = matcher().match_one(FakePolymarket([m], {"c1": history}), shown_btc80(83),
                              OCT9, at(OCT9, 14000))
    assert row["status"] == "review"


def test_the_odds_shown_were_confirmed_at_their_minute_and_first_seen_is_kept_apart():
    # The first sighting only fits a price from minutes before; the second
    # fits the market at its own minute. The page shows the second's odds,
    # and still says the market was first on screen at the first.
    history = steps(OCT9, (3500, 0.83), (3900, 0.70), (4000, 0.70), (4300, 0.70))
    m = {**MARKETS["5170738"], "clobTokenIds": '["c1", "c2"]'}
    shown = {"text": "Will Bitcoin dip to $80,000 in October?",
             "sightings": [[t, at(OCT9, t).isoformat(), y, True,
                            "Will Bitcoin dip to $80,000 in October?"]
                           for t, y in ((4000.0, 83), (4300.0, 70))]}
    row = matcher().match_one(FakePolymarket([m], {"c1": history}), shown, OCT9, at(OCT9, 14000))
    assert row["status"] == "matched"
    assert row["first"]["yes"] == 70 and row["first"]["t"] == 4300.0
    assert row["seen_from"]["t"] == 4000.0
    assert abs(row["price_then"] - row["first"]["yes"]) <= pm.ODDS_TOLERANCE


def test_no_at_the_start_of_a_question_is_a_word_not_the_icon():
    got = pm.readings("2026? YES 40% @ No change in Bank of Mexico’s interest rates after "
                      "November 2026 meeting? YES 61%")
    assert got[-1].text.startswith("No change in Bank of Mexico")


def test_what_is_searched_for_starts_at_the_first_word():
    q = matcher().queries("%2 NATO x Russia military clash by October 31,2026?")
    assert q[0] == "NATO x Russia military clash by October 31,2026"
    assert matcher().queries("Will the Fed cut?") == ["Will the Fed cut", "Fed cut"]

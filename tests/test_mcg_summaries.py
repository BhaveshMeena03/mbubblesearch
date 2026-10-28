"""MCG's episode notes: the writer that ships data/mcg_summaries.json.gz.

The 404 notes written when MCG Search was its own service put a quarter of
their topics on a real line, and on Arrow Finance's episode every topic was
in minutes rather than seconds, so all nine played from the first forty
seconds. These are the cases that must not come back.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import summarize_mcg as sm  # noqa: E402


def seg(t, text):
    return {"t": float(t), "text": text}


# Arrow Finance's interview, in short.
SEGMENTS = [
    seg(2, "We got the Arrow team in the back rooms again."),
    seg(132, "Ash was at Robin Hood crypto in 2021 on the wallet launch."),
    seg(1325, "You deposit tokenized equities as collateral and borrow AUSD."),
    seg(1423, "LTVs go from 90% for stablecoins down to 55% for tokenized equities."),
    seg(2098, "The Sherlock audit wraps up and mainnet is one to two weeks out."),
    seg(2287, "Morpho is already on Robinhood with 237 million in TVL."),
    seg(2820, "Thanks for coming on, guys."),
]
ROW = {"id": "4eErwRiL8VE", "seconds": 2830, "format": "interview",
       "title": "Arrow Finance- The gateway for Robinhood DeFi"}
TLDR = "Arrow lets people borrow AUSD against tokenized equities, with LTVs down to 55% for stocks."
GOOD = [("0:02:12", "Ash worked at Robinhood crypto in 2021 on the wallet launch."),
        ("0:22:05", "Deposit tokenized equities as collateral and borrow AUSD."),
        ("0:23:43", "LTVs run from 90% for stablecoins to 55% for tokenized equities."),
        ("0:34:58", "Mainnet is one to two weeks out once the Sherlock audit is done."),
        ("0:38:07", "Morpho is already on Robinhood with $237M in TVL.")]


def reply(topics, tldr=TLDR):
    return json.dumps({"tldr": tldr, "topics": [{"at": at, "text": text} for at, text in topics]})


class Replies:
    def __init__(self, *raws):
        self.raws, self.sent = list(raws), []
        self.messages = self

    def stream(self, **kwargs):
        client = self
        client.sent.append(kwargs)

        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get_final_message(self):
                text = client.raws.pop(0)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        return Stream()


def test_the_prompt_asks_for_the_printed_timestamp_not_seconds():
    assert '{"at": "0:02:05"' in sm.SYSTEM
    assert "copied exactly as it is printed" in sm.SYSTEM
    assert '"t":' not in sm.SYSTEM and "seconds from the start" not in sm.SYSTEM


def test_copied_timestamps_come_back_as_the_seconds_of_their_lines():
    client = Replies(reply(GOOD))
    got = asyncio.run(sm.summarize(client, "m", ROW, SEGMENTS))
    assert [t["t"] for t in got["topics"]] == [132, 1325, 1423, 2098, 2287]
    assert client.sent[0]["system"] == sm.SYSTEM


def test_minutes_written_where_seconds_belong_are_refused():
    """Arrow Finance's notes: 21 for a topic at 22:05, 34 for one at 34:08."""
    minutes = [("0:00:21", GOOD[1][1]), ("0:00:23", GOOD[2][1]),
               ("0:00:34", GOOD[3][1]), ("0:00:38", GOOD[4][1])]
    client = Replies(reply(minutes), reply(minutes))
    with pytest.raises(ValueError, match="no usable summary"):
        asyncio.run(sm.summarize(client, "m", ROW, SEGMENTS))


def test_the_title_spells_the_project_the_captions_mangle():
    """The prompt says to take a project's name from the title."""
    segments = SEGMENTS[:1] + [seg(20, "Welcome the arrow fy nance team.")] + SEGMENTS[1:]
    topics = GOOD + [("0:00:20", "The host welcomes the team behind Arrow Finance.")]
    got = asyncio.run(sm.summarize(Replies(reply(topics)), "m", ROW, segments))
    assert [t["t"] for t in got["topics"]] == [20, 132, 1325, 1423, 2098, 2287]


def test_a_tldr_that_opens_on_an_address_is_refused():
    """One summary opened "Dude, founder of RatSpeak"."""
    client = Replies(reply(GOOD, tldr="Dude, founder of Arrow, lets people borrow AUSD against equities."))
    with pytest.raises(ValueError, match="not a name"):
        asyncio.run(sm.summarize(client, "m", ROW, SEGMENTS))
    assert not sm.looks_misattributed(TLDR)


def test_transcripts_are_read_from_mcg_searchs_episode_file(tmp_path):
    path = tmp_path / "episodes.json"
    path.write_text(json.dumps([
        {"episode_id": "4eErwRiL8VE", "title": "x",
         "segments": [{"t": 0.16, "text": "gm"}, {"t": 6.56, "text": "ok"}]},
        {"title": "no id"}]))
    assert sm.transcripts_from(path) == {
        "4eErwRiL8VE": [{"t": 0.16, "text": "gm"}, {"t": 6.56, "text": "ok"}]}


def test_it_writes_the_file_the_server_reads():
    from app import main
    assert sm.OUT == main.MCG_SUMMARIES
    assert sm.SHELF == main.MCG_INDEX

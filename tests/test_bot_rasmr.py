"""The bot answers rasmr questions from rasmr's archive.

Built the way ThreadGuy's routing was, and tested the same way: on which
index the bot actually searches, not only on corpus_for, because the two
can disagree. He sits between three other archives by name. He was a
guest on Market Bubble, he co-hosts a show on ThreadGuy's channel, and
Elon comes up on his streams, so the tests that matter most are the ones
where his name and one of theirs are in the same question.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import mcg_on_x  # noqa: E402
from app.x_api import Mention  # noqa: E402
from app.x_bot import (  # noqa: E402
    MentionBot,
    corpus_for,
    format_reply,
    rasmr_thread,
    routed_on_evidence,
)

MISS = "I couldn't find that in the archive."
FOUND = "Around 20:56 he said he sold after the catalyst."


class Recorder:
    def __init__(self, answer=FOUND):
        self.asked, self._answer = [], answer

    async def search(self, q, **kw):
        self.asked.append(q)
        answer = self._answer

        class R:
            hits = []
            refused = False
        R.answer = answer
        return R()


class Client:
    bot_user_id = "1"

    async def post(self, *a, **k):
        raise AssertionError("no posting")


def mention(text, n="1"):
    return Mention(id=n, text=text, author_id="a" + n, conversation_id=n,
                   author_verified=True, author_verified_type="blue")


@pytest.mark.parametrize("question", [
    "@mbubbleSearch what did rasmr say about hype",
    "@mbubbleSearch what did @rasmr_eth say about memecoins",
    "@mbubbleSearch why did rasmr sell all his hype",
    "@mbubbleSearch what did rasmr say about ansem",
    "@mbubbleSearch what did rasmr say about elon",
    "@mbubbleSearch what did rasmr say about threadguy",
    "@mbubbleSearch rasmr on the stream about catalysts",
    "@mbubbleSearch what is rasmr's take on cupsey",
])
def test_naming_rasmr_routes_to_his_archive(question):
    assert corpus_for(question) == "rasmr"


@pytest.mark.parametrize("question, expected", [
    # Named second, the one named first is being asked.
    ("@mbubbleSearch what did ansem say about rasmr", "podcast"),
    ("@mbubbleSearch what did elon say about rasmr", "elon"),
    ("@mbubbleSearch what did @elonmusk say about @rasmr_eth", "elon"),
    ("@mbubbleSearch what did threadguy say about rasmr", "threadguy"),
    ("@mbubbleSearch did banks and rasmr talk about zcash", "podcast"),
    # And nothing that only looks like him.
    ("@mbubbleSearch what did ansem say about zcash", "podcast"),
    ("@mbubbleSearch what did @elonmusk say about mars", "elon"),
    ("@mbubbleSearch what did threadguy say about hyperliquid", "threadguy"),
    ("@mbubbleSearch who is rasmrfan", "podcast"),
])
def test_everything_else_routes_as_before(question, expected):
    assert corpus_for(question) == expected


def test_naming_him_is_evidence_and_not_a_guess():
    """A miss on a question that named him has been answered: it is not in
    his archive, and asking the others would answer something not asked."""
    assert routed_on_evidence("what did rasmr say about the fed")
    assert routed_on_evidence("what did @rasmr_eth say about the fed")
    assert not routed_on_evidence("what was said about the fed")


def test_his_thread_is_read_off_the_leading_handles_only():
    assert rasmr_thread("@rasmr_eth @mbubbleSearch what did he say about hype")
    assert rasmr_thread("@mbubbleSearch @cupseyy @rasmr_eth when does he sell")
    # typed mid-sentence is not a thread hint (it is a name, handled above)
    assert not rasmr_thread("@mbubbleSearch is @rasmr_eth right about this")
    assert not rasmr_thread("@mbubbleSearch @blknoiz06 what about zcash")
    assert not rasmr_thread("@mbubbleSearch @rasmr_ethan what about zcash")


def build(tmp_path, show, rasmr, tg=None, **kw):
    return MentionBot(Client(), show, rasmr_index=rasmr, threadguy_index=tg,
                      post_limit=1500, state_path=tmp_path / "state.json", **kw)


@pytest.mark.anyio
async def test_a_question_naming_rasmr_reaches_his_archive(tmp_path):
    show, rasmr, tg = Recorder(), Recorder(), Recorder()
    await build(tmp_path, show, rasmr, tg).compose(
        mention("@mbubbleSearch what did rasmr say about hype"))
    assert rasmr.asked and not show.asked and not tg.asked


@pytest.mark.anyio
async def test_a_question_naming_ansem_first_stays_on_the_show(tmp_path):
    show, rasmr = Recorder(), Recorder()
    await build(tmp_path, show, rasmr).compose(
        mention("@mbubbleSearch what did ansem say about rasmr"))
    assert show.asked and not rasmr.asked


@pytest.mark.anyio
async def test_a_question_naming_elon_first_goes_to_elon(tmp_path):
    show, rasmr, elon = Recorder(), Recorder(), Recorder()
    await build(tmp_path, show, rasmr, elon_index=elon).compose(
        mention("@mbubbleSearch what did elon say about rasmr"))
    assert elon.asked and not rasmr.asked and not show.asked


@pytest.mark.anyio
async def test_a_bare_question_under_his_post_goes_to_him(tmp_path):
    show, rasmr = Recorder(), Recorder()
    await build(tmp_path, show, rasmr).compose(mention(
        "@mbubbleSearch @rasmr_eth when does he sell a memecoin"))
    assert rasmr.asked and not show.asked


@pytest.mark.anyio
async def test_a_show_question_in_his_thread_stays_on_the_show(tmp_path):
    show, rasmr = Recorder(), Recorder()
    await build(tmp_path, show, rasmr).compose(mention(
        "@mbubbleSearch @rasmr_eth what did ansem say about zcash"))
    assert show.asked and not rasmr.asked


@pytest.mark.anyio
async def test_a_thread_carrying_both_hosts_tries_threadguy_first(tmp_path):
    """Most likely under their hot seat, which is on ThreadGuy's channel."""
    show, rasmr, tg = Recorder(), Recorder(), Recorder()
    await build(tmp_path, show, rasmr, tg).compose(mention(
        "@mbubbleSearch @notthreadguy @rasmr_eth what did he say about hype"))
    assert tg.asked and not rasmr.asked and not show.asked


@pytest.mark.anyio
async def test_a_miss_in_his_thread_goes_on_to_the_broadcast(tmp_path):
    """Routed there by the thread alone, so nothing in the question chose
    it, and a miss is asked of the other shelves, the broadcast included."""
    show, rasmr = Recorder(), Recorder(MISS)
    await build(tmp_path, show, rasmr).compose(mention(
        "@mbubbleSearch @rasmr_eth what was said about the fed"))
    assert rasmr.asked and show.asked


@pytest.mark.anyio
async def test_a_miss_on_a_question_naming_him_is_not_asked_elsewhere(tmp_path):
    show, rasmr = Recorder(), Recorder(MISS)
    await build(tmp_path, show, rasmr).compose(mention(
        "@mbubbleSearch what did rasmr say about the fed"))
    assert rasmr.asked and not show.asked


@pytest.mark.anyio
async def test_a_deploy_without_the_archive_answers_from_the_show(tmp_path):
    show = Recorder()
    await build(tmp_path, show, None).compose(
        mention("@mbubbleSearch what did rasmr say about hype"))
    assert show.asked


def test_the_running_bot_is_handed_the_rasmr_archive():
    main = (ROOT / "app" / "main.py").read_text()
    wiring = main.split("bot = MentionBot(")[1].split("\n    )")[0]
    assert 'rasmr_index=getattr(app.state, "rasmr", None)' in wiring


# --- the link -----------------------------------------------------------------

PAIRS = json.loads((ROOT / "data" / "rasmr_broadcast_links.json").read_text())


@dataclass
class RasmrHit:
    deep_link: str
    title: str = "LIVE: Talkin Markets"
    timestamp: str = "40:00"
    text: str = "sell after the catalyst"
    text_ts: str = "[40:00] sell after the catalyst"


@pytest.fixture
def shipped_pairs():
    mcg_on_x._pairs.cache_clear()
    yield
    mcg_on_x._pairs.cache_clear()


def test_a_stream_he_also_ran_on_x_is_linked_there(shipped_pairs):
    """The reply is on X, and so is the reader. Same rule and same table
    shape as MCG's: X's second is YouTube's plus the offset."""
    vid, pair = next(iter(PAIRS.items()))
    second = 2400 + pair["offset"]
    reply = format_reply("He said to sell after the catalyst.",
                         [RasmrHit(f"https://www.youtube.com/watch?v={vid}&t=2400s")],
                         include_links=True)
    assert f"{pair['broadcast']}?t={second}" in reply
    assert "youtube.com" not in reply


def test_a_stream_that_was_never_paired_keeps_its_youtube_link(shipped_pairs):
    unpaired = "https://www.youtube.com/watch?v=A8Zo5iXul_8&t=39s"
    assert "A8Zo5iXul_8" not in PAIRS
    assert mcg_on_x.on_x(unpaired) == unpaired


def test_a_stream_that_ran_only_on_x_keeps_its_x_link(shipped_pairs):
    link = "https://x.com/i/broadcasts/1rGmqpnbjvnGy?t=5781"
    assert mcg_on_x.on_x(link) == link


def test_a_broken_rasmr_file_costs_only_his_links(tmp_path, monkeypatch, shipped_pairs):
    broken = tmp_path / "rasmr_broadcast_links.json"
    broken.write_text("{not json")
    monkeypatch.setattr(mcg_on_x, "_RASMR_PATH", broken)
    mcg_on_x._pairs.cache_clear()
    vid = next(iter(PAIRS))
    link = f"https://www.youtube.com/watch?v={vid}&t=2400s"
    assert mcg_on_x.on_x(link) == link
    # MCG's own pairs still load.
    mcg = json.loads((ROOT / "data" / "mcg_broadcast_links.json").read_text())
    # One with a single offset: a show whose clocks moved may leave 2400s
    # unmapped, and that keeps its YouTube link on purpose.
    some, pair = next((k, p) for k, p in mcg.items() if "sections" not in p)
    assert mcg_on_x.on_x(f"https://www.youtube.com/watch?v={some}&t=2400s") \
        .startswith(pair["broadcast"])

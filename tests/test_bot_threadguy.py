"""The bot answers ThreadGuy questions from ThreadGuy's archive.

It did not. The archive went live on the site, the account posted a
ThreadGuy clip ending "tag me and ask me anything from threadguy's
streams", and the bot had never been handed the index: "what did
threadguy say about hyperliquid" was routed to Market Bubble.

As with the Musk routing, the bot is tested on which index it actually
searches, not only on corpus_for, because the two can disagree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.schemas import PodcastHit  # noqa: E402
from app.x_api import Mention  # noqa: E402
from app.x_bot import MentionBot, corpus_for, threadguy_thread  # noqa: E402

MISS = "I couldn't find that in the archive."
FOUND = "Around 20:56 he said it was the best exchange."


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
    "@mbubbleSearch what did threadguy say about hyperliquid",
    "@mbubbleSearch what did @notthreadguy say about zcash",
    "@mbubbleSearch what did thread guy predict about leopold",
    "@mbubbleSearch what did threadguy say about ansem",
    "@mbubbleSearch threadguy on the stream about robinhood",
])
def test_naming_threadguy_routes_to_his_archive(question):
    assert corpus_for(question) == "threadguy"


@pytest.mark.parametrize("question, expected", [
    # Ansem named first: the asker wants Ansem's view.
    ("@mbubbleSearch what did ansem say about threadguy", "podcast"),
    # "counterparty" is ordinary finance, not his network.
    ("@mbubbleSearch what is counterparty risk on hyperliquid", "podcast"),
    ("@mbubbleSearch what did @elonmusk say about mars", "elon"),
    ("@mbubbleSearch what did ansem say about zcash", "podcast"),
])
def test_everything_else_routes_as_before(question, expected):
    assert corpus_for(question) == expected


def test_his_thread_is_read_off_the_leading_handles_only():
    under_our_post = ("@mbubbleSearch @blurr @papertrade_xyz @notthreadguy "
                      "@counterpartytv what did he say about hyperliquid")
    assert threadguy_thread(under_our_post)
    assert threadguy_thread("@counterpartytv @mbubbleSearch who was on today")
    # typed mid-sentence is not a thread hint (it is a name, handled above)
    assert not threadguy_thread("@mbubbleSearch is @notthreadguy right")
    assert not threadguy_thread("@mbubbleSearch @blknoiz06 what about zcash")


def build(tmp_path, show, tg, **kw):
    return MentionBot(Client(), show, threadguy_index=tg, post_limit=1500,
                      state_path=tmp_path / "state.json", **kw)


@pytest.mark.anyio
async def test_a_question_naming_threadguy_reaches_his_archive(tmp_path):
    show, tg = Recorder(), Recorder()
    await build(tmp_path, show, tg).compose(
        mention("@mbubbleSearch what did threadguy say about hyperliquid"))
    assert tg.asked and not show.asked


@pytest.mark.anyio
async def test_a_bare_question_under_our_threadguy_post_goes_to_him(tmp_path):
    """The post that invited it: "tag me and ask me anything"."""
    show, tg = Recorder(), Recorder()
    await build(tmp_path, show, tg).compose(mention(
        "@mbubbleSearch @blurr @papertrade_xyz @notthreadguy @counterpartytv "
        "what did he say about hyperliquid"))
    assert tg.asked and not show.asked


@pytest.mark.anyio
async def test_a_show_question_in_his_thread_stays_on_the_show(tmp_path):
    show, tg = Recorder(), Recorder()
    await build(tmp_path, show, tg).compose(mention(
        "@mbubbleSearch @notthreadguy what did ansem say about zcash"))
    assert show.asked and not tg.asked


@pytest.mark.anyio
async def test_a_miss_in_his_thread_goes_on_to_the_broadcast(tmp_path):
    """Routed there by the thread alone, so nothing in the question chose
    it, and a miss is asked of the other shelves, the broadcast included."""
    show, tg = Recorder(), Recorder(MISS)
    await build(tmp_path, show, tg).compose(mention(
        "@mbubbleSearch @notthreadguy what was said about the fed"))
    assert tg.asked and show.asked


@pytest.mark.anyio
async def test_a_deploy_without_the_archive_answers_from_the_show(tmp_path):
    show = Recorder()
    await build(tmp_path, show, None).compose(
        mention("@mbubbleSearch what did threadguy say about hyperliquid"))
    assert show.asked


def test_the_running_bot_is_handed_the_threadguy_archive():
    main = (ROOT / "app" / "main.py").read_text()
    wiring = main.split("bot = MentionBot(")[1].split("\n    )")[0]
    assert 'threadguy_index=getattr(app.state, "threadguy", None)' in wiring


class Flaky:
    """Empty on the first call, an answer on the second."""

    def __init__(self):
        self.calls = 0

    async def search(self, q, **kw):
        self.calls += 1
        answer = "" if self.calls == 1 else FOUND

        class R:
            hits = [PodcastHit(episode_id="e", title="Hot seat", start_seconds=s,
                               timestamp="0:00", score=0.5, text="t",
                               deep_link=f"https://www.youtube.com/watch?v=nbQ_Cl4UmRc&t={s}s")
                    for s in (460, 1200, 2400)]
            refused = False
        R.answer = answer
        return R()


@pytest.mark.anyio
async def test_an_empty_answer_is_asked_again_instead_of_going_quiet(tmp_path):
    show, tg = Recorder(), Flaky()
    await build(tmp_path, show, tg).compose(
        mention("@mbubbleSearch what did threadguy say about hyperliquid"))
    assert tg.calls == 2


def test_deepseek_gets_room_to_reason_before_it_writes():
    """It spent the whole 1,024 tokens reasoning and returned no text, and
    an empty answer makes the bot stay quiet: two of three questions under
    a Tulip King post went unanswered. Haiku, which answers the site, does
    not reason first and keeps the smaller budget."""
    from app.podcast import _answer_budget
    assert _answer_budget("deepseek-v4-1-flash", 1024) == 4096
    assert _answer_budget("claude-haiku-4-5", 1024) == 1024
    assert _answer_budget("deepseek-v4-1-flash", 8000) == 8000

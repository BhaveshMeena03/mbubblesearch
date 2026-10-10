"""A handle X carried into a reply is not somebody tagging the account.

10 October 2026. A post said it was the author's birthday and tagged ten
accounts, this one among them. A friend replied "happy birthday brother,
what are you doing today". X puts every handle from the post above in
front of a reply, so the reply arrived as a mention, it had a question in
it, and the account answered: a line about what it is and a clip of a
cake on the show, to a man wishing his friend a happy birthday.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.x_api import Mention, only_carried  # noqa: E402
from app.x_bot import MentionBot, carried_into_somebody_elses_reply  # noqa: E402

US = "2091231768200499200"
# The reply as X returned it, positions and all.
BIRTHDAY = {
    "id": "2108802720669094199",
    "text": ("@AJMetaX1 @blknoiz06 @alchemifydotfun @M1lanX0r404 "
             "@Bagphilosopher @WatchfulWeb3 @RAP3MEFR @Sizedgg @mbubbleSearch "
             "@tradingbubblesz happy birthday brother 🫶🏼 \n\n"
             "what are you doing today"),
    "display_text_range": [132, 184],
    "in_reply_to_user_id": "974224548685139975",
    "entities": {"mentions": [
        {"username": "AJMetaX1", "start": 0, "end": 9, "id": "974224548685139975"},
        {"username": "blknoiz06", "start": 10, "end": 20, "id": "1"},
        {"username": "mbubbleSearch", "start": 100, "end": 114, "id": US},
        {"username": "tradingbubblesz", "start": 115, "end": 131, "id": "2"},
    ]},
}


def test_a_handle_in_front_of_the_typed_words_was_carried():
    assert only_carried(BIRTHDAY, US)


def test_a_handle_somebody_typed_is_a_tag():
    """Replying to Ansem and writing our name: X adds his handle, the
    person added ours."""
    typed = {"text": "@blknoiz06 @mbubbleSearch what did he say about hype",
             "display_text_range": [11, 52],
             "entities": {"mentions": [
                 {"username": "blknoiz06", "start": 0, "end": 10, "id": "1"},
                 {"username": "mbubbleSearch", "start": 11, "end": 25, "id": US}]}}
    assert not only_carried(typed, US)


def test_named_twice_once_by_hand_is_a_tag():
    both = {"display_text_range": [30, 80],
            "entities": {"mentions": [
                {"username": "mbubbleSearch", "start": 10, "end": 24, "id": US},
                {"username": "mbubbleSearch", "start": 40, "end": 54, "id": US}]}}
    assert not only_carried(both, US)


def test_without_the_range_nothing_changes():
    """Unknown is treated the way everything was before."""
    assert not only_carried({"entities": BIRTHDAY["entities"]}, US)
    assert not only_carried(BIRTHDAY, "")
    assert not only_carried({"display_text_range": [5, 9]}, US)


def _mention(**fields) -> Mention:
    base = {"id": "9", "text": BIRTHDAY["text"], "author_id": "7",
            "conversation_id": "2108801974468796779"}
    return Mention(**{**base, **fields})


def test_only_a_reply_to_somebody_else_is_left_alone():
    carried = _mention(carried=True, in_reply_to_user_id="974224548685139975")
    assert carried_into_somebody_elses_reply(carried, US)
    # A reply to one of our posts inherits our handle the same way and is
    # for us.
    to_us = _mention(carried=True, in_reply_to_user_id=US)
    assert not carried_into_somebody_elses_reply(to_us, US)
    typed = _mention(carried=False, in_reply_to_user_id="974224548685139975")
    assert not carried_into_somebody_elses_reply(typed, US)
    assert not carried_into_somebody_elses_reply(carried, "")


def _bot() -> MentionBot:
    bot = MentionBot.__new__(MentionBot)
    bot._cited_episode = None
    bot._speaker_ids = {}
    bot._client = types.SimpleNamespace(bot_user_id=US, own_threads=set())
    return bot


def test_the_birthday_reply_gets_nothing(caplog):
    mention = _mention(carried=True, in_reply_to_user_id="974224548685139975",
                       replied_to_id="2108801974468796779")
    with caplog.at_level(logging.INFO, logger="app.x_bot"):
        assert asyncio.run(_bot().compose(mention)) is None
    assert "only inherited our handle" in caplog.text


def test_the_same_words_with_our_name_typed_are_still_heard(caplog):
    mention = _mention(text="@AJMetaX1 @mbubbleSearch what did ansem say about zcash",
                       carried=False, in_reply_to_user_id="974224548685139975")
    with caplog.at_level(logging.INFO, logger="app.x_bot"):
        try:
            asyncio.run(_bot().compose(mention))
        except AttributeError:
            pass        # past the guard, into retrieval this test has not stubbed
    assert "only inherited our handle" not in caplog.text


def test_the_mentions_read_asks_where_the_typed_words_begin():
    source = (ROOT / "app" / "x_api.py").read_text()
    assert "display_text_range,entities" in source
    assert "carried=only_carried(m, self.bot_user_id)" in source

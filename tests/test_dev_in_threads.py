"""The dev replying in somebody's thread is not asking this account anything.

On 15 Oct 2026 he pointed a streamer at the account under the streamer's
own post, twice, and both times the bot answered with an unrelated passage
that tagged the streamer and his friends. His replies are now left alone;
a post he starts himself is answered like anyone's, so a public demo works.
"""

import asyncio
import logging
import types

from app.x_api import Mention
from app.x_bot import MentionBot

DEV = "1070767274808696832"


def bot(dev=DEV):
    b = MentionBot.__new__(MentionBot)
    b._cited_episode = None
    b._speaker_ids = {}
    b._client = types.SimpleNamespace(own_threads=set(), bot_user_id="42")
    b._dev = dev
    return b


def mention(text, author=DEV, conversation="2110565680437235843",
            mid="2110580290855035053", replied_to="2110579599965102469"):
    return Mention(id=mid, text=text, author_id=author, conversation_id=conversation,
                   created_at="", author_verified=True, author_verified_type="blue",
                   replied_to_id=replied_to)


def stayed_quiet_as_dev(b, m, caplog) -> bool:
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.x_bot"):
        try:
            result = asyncio.run(b.compose(m))
        except AttributeError:
            # Fell through to retrieval, which these stubs do not have.
            return False
    return result is None and "dev replying in a thread" in caplog.text


def test_both_posts_from_that_morning_get_no_reply(caplog):
    for text in ("@rasmr_eth @fearings_ @jackduval Rasmr @mbubbleSearch does "
                 "everything n make your stream searchable \nCheck dms 🫡",
                 "@mbubbleSearch @rasmr_eth @mileage59 @rasmr_eth Let me know what you think ?",
                 # And a real question, because in his reply it is still said
                 # to the person he is replying to.
                 "@rasmr_eth @mbubbleSearch what did ansem say about hype"):
        assert stayed_quiet_as_dev(bot(), mention(text), caplog), text


def test_a_post_he_starts_himself_is_still_answered(caplog):
    m = mention("@mbubbleSearch what did ansem say about hype",
                conversation="2110590000000000000", mid="2110590000000000000",
                replied_to="")
    assert not stayed_quiet_as_dev(bot(), m, caplog)


def test_anybody_else_replying_in_a_thread_is_unaffected(caplog):
    m = mention("@mbubbleSearch what did ansem say about hype", author="7")
    assert not stayed_quiet_as_dev(bot(), m, caplog)


def test_an_empty_setting_turns_it_off(caplog):
    m = mention("@mbubbleSearch what did ansem say about hype")
    assert not stayed_quiet_as_dev(bot(dev=""), m, caplog)


def test_the_live_setting_names_his_account():
    from app.config import Settings
    assert Settings.model_fields["x_bot_dev_id"].default == DEV


def test_asked_to_introduce_itself_in_his_reply_it_does():
    """22 Oct 2026, under @AJMetaX1's post: silence, before this."""
    b = bot()
    b._contract_address = b._token_label = None
    b._site = "search.lexthedev.com"
    text = ("@AJMetaX1 @Kaiz_294 the archive grows everyday new episodes "
            "indexed everyday  \u2764\ufe0f \U0001fae1 \n@mbubbleSearch introduce yourself")
    m = mention(text, conversation="2113268083150516670",
                mid="2113269674049958283", replied_to="2113268083150516670")
    reply = asyncio.run(b.compose(m))
    from app.x_bot import _INTRO_PHRASINGS
    assert reply and any(v.split("\n")[0] in reply for v in _INTRO_PHRASINGS)


def test_the_self_description_names_all_six_archives():
    from app.x_bot import _ABOUT_PHRASINGS, _INTRO_PHRASINGS
    for text in _INTRO_PHRASINGS + _ABOUT_PHRASINGS:
        for archive in ("Market Bubble", "MCG Live", "ThreadGuy", "rasmr",
                        "Elon Musk", "The Record"):
            assert archive in text, (archive, text[:60])
        assert "\u2014" not in text and "The three" not in text


def test_the_description_links_the_front_door_not_one_show():
    from app.x_bot import _front_door, about_answer, automation_answer
    for reply in (automation_answer("introduce yourself", "search.lexthedev.com"),
                  about_answer("what is marketbubblesearch", "search.lexthedev.com")):
        assert reply.rstrip().endswith("search.lexthedev.com/home"), reply[-40:]
    assert _front_door("search.lexthedev.com/") == "search.lexthedev.com/home"
    assert _front_door("example.com/somewhere") == "example.com/somewhere"

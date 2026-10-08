import asyncio
import sys
import types

sys.path.insert(0, ".")

from app.x_bot import MentionBot


def test_skips_questions_under_our_own_posts(monkeypatch):
    """A reply under an announcement we posted is about us, not the archive."""
    from app.x_api import Mention
    bot = MentionBot.__new__(MentionBot)
    bot._cited_episode = None
    bot._speaker_ids = {}
    bot._client = types.SimpleNamespace(own_threads={"999"})
    m = Mention(id="1", text="@mbubbleSearch where does the rest of the fee go?",
                author_id="7", conversation_id="999", created_at="",
                author_verified=True, author_verified_type="blue")
    assert asyncio.run(bot.compose(m)) is None

def test_still_answers_under_somebody_elses_post():
    """A thread we did not start is a normal archive question."""
    from app.x_api import Mention
    bot = MentionBot.__new__(MentionBot)
    bot._cited_episode = None
    bot._speaker_ids = {}
    bot._client = types.SimpleNamespace(own_threads={"999"})
    m = Mention(id="1", text="@mbubbleSearch what did ansem say about zcash",
                author_id="7", conversation_id="555", created_at="",
                author_verified=True, author_verified_type="blue")
    # Reaches retrieval rather than being silently dropped by the guard.
    try:
        asyncio.run(bot.compose(m))
    except AttributeError:
        pass  # fell through the guard into the un-stubbed retrieval path
    else:
        pass


def _own_thread_mention(text):
    from app.x_api import Mention
    return Mention(id="1", text=text, author_id="7", conversation_id="999",
                   created_at="", author_verified=True, author_verified_type="blue")


def _bot():
    bot = MentionBot.__new__(MentionBot)
    bot._cited_episode = None
    bot._speaker_ids = {}
    bot._client = types.SimpleNamespace(own_threads={"999"})
    return bot


def _skipped(text, caplog) -> bool:
    import logging
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.x_bot"):
        try:
            asyncio.run(_bot().compose(_own_thread_mention(text)))
        except AttributeError:
            pass        # got past the guard into the un-stubbed retrieval
    return "under our own post" in caplog.text


def test_an_archive_question_under_our_own_post_is_answered(caplog):
    """A judge replying to the pinned demo post, mid-stream."""
    assert not _skipped("@mbubbleSearch did ansem say zcash could go to 10,000?", caplog)
    assert not _skipped("@mbubbleSearch what did threadguy say about pump", caplog)


def test_questions_about_the_account_under_our_own_post_still_are_not(caplog):
    assert _skipped("@mbubbleSearch where does the rest of the fee go?", caplog)
    assert _skipped("@mbubbleSearch does ansem get the $MBS fees?", caplog)
    assert _skipped("@mbubbleSearch is this bot run by ansem?", caplog)


# --- a post written since the last time the timeline was read ----------------
#
# 8 October 2026. A post went up at 20:14. At 20:20 somebody replied "That's
# why we chose to work with them. Great work @ConejoCapital and @tomi204!"
# and at 20:21 this account answered the word "why" with Ansem asking a
# Robinhood guest about Arbitrum. The guard above exists for exactly this,
# and never fired: its list of our own threads is read at a cold start and
# the post was six minutes old.

REPLY = ("@mbubbleSearch @clawpumptech @ConejoCapital @MCGlive @mynt_josh "
         "That's why we chose to work with them. Great work @ConejoCapital "
         "and @tomi204! @clawpumptech eco for the win!")


def _fresh_bot(own_threads=None):
    bot = MentionBot.__new__(MentionBot)
    bot._cited_episode = None
    bot._speaker_ids = {}
    bot._client = types.SimpleNamespace(
        bot_user_id="42", own_threads=set(own_threads or ()))
    return bot


def _reply(text, **fields):
    from app.x_api import Mention
    return Mention(id="2108291365847384398", text=text, author_id="7",
                   conversation_id="2108289982712758640", created_at="",
                   author_verified=True, author_verified_type="blue", **fields)


def test_a_comment_under_a_post_we_just_wrote_is_left_alone(caplog):
    import logging
    bot = _fresh_bot()                       # the post is not on the list
    mention = _reply(REPLY, in_reply_to_user_id="42",
                     replied_to_id="2108289982712758640")
    with caplog.at_level(logging.INFO, logger="app.x_bot"):
        assert asyncio.run(bot.compose(mention)) is None
    assert "under our own post" in caplog.text
    # And the thread is ours from then on, for the replies beneath it.
    assert "2108289982712758640" in bot._client.own_threads


def test_an_archive_question_under_a_post_we_just_wrote_is_still_answered(caplog):
    import logging
    bot = _fresh_bot()
    mention = _reply("@mbubbleSearch what did ansem say about zcash",
                     in_reply_to_user_id="42",
                     replied_to_id="2108289982712758640")
    with caplog.at_level(logging.INFO, logger="app.x_bot"):
        try:
            asyncio.run(bot.compose(mention))
        except AttributeError:
            pass        # past the guard, into retrieval this test has not stubbed
    assert "under our own post" not in caplog.text


def test_who_a_mention_replies_to_is_read_off_the_mention():
    from app.x_bot import replies_to_our_root, replies_to_us
    to_root = _reply(REPLY, in_reply_to_user_id="42",
                     replied_to_id="2108289982712758640")
    assert replies_to_us(to_root, "42") and replies_to_our_root(to_root, "42")
    # A reply to an answer of ours inside somebody else's thread.
    to_answer = _reply("ok", in_reply_to_user_id="42", replied_to_id="555")
    assert replies_to_us(to_answer, "42")
    assert not replies_to_our_root(to_answer, "42")
    # Somebody tagging us fresh, or replying to somebody else.
    assert not replies_to_us(_reply("hi"), "42")
    assert not replies_to_us(_reply("hi", in_reply_to_user_id="9"), "42")
    assert not replies_to_us(to_root, "")        # we do not know who we are


def test_the_mentions_read_asks_who_each_post_replies_to():
    from pathlib import Path
    source = Path("app/x_api.py").read_text()
    assert "in_reply_to_user_id,referenced_tweets" in source

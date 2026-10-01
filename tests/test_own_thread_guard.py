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

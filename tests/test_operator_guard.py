"""Questions for the person who built this are not archive questions.

Kelly asked "Really curious though, what made you start this?" in a thread
where Lex had just introduced himself. The bot answered with why Ansem and
Banks started Market Bubble, off the archive.

Silence rather than a description: somebody asking Lex why he built it is
asking Lex, and the account cannot answer for him.
"""
from app.x_bot import asks_the_operator

FOR_LEX = [
    "what made you start this?", "why did you build this",
    "how did you start this", "what's your story",
    "when did you launch this", "how long have you been building this",
    "what got you into this",
]
# Impersonal "you" is present tense and has to keep working, as does any
# origin question about somebody in the archive.
FOR_THE_ARCHIVE = [
    "how do you buy hyperliquid",
    "what did ansem say about zcash",
    "why did pumpfun never airdrop",
    "how did drip get to 300 million collectibles",
    "what made ansem bullish on solana",
    "when did fink change his mind",
]


def test_operator_questions_are_recognised():
    for q in FOR_LEX:
        assert asks_the_operator(q), q


def test_archive_questions_are_untouched():
    for q in FOR_THE_ARCHIVE:
        assert not asks_the_operator(q), q

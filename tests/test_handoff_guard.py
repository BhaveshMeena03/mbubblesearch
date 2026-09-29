"""A handoff is not a question.

Lex wrote "here you go Kelly" with a link. The bot searched for the only
proper noun in the sentence, found an MCG project called Kelly Claude, and
answered a thread about this search engine with a pitch for an autonomous
"blackbox money printer".
"""
from app.x_bot import asks_something, has_a_known_intent, is_a_handoff, question_from


def skipped(text: str) -> bool:
    """The condition compose() applies."""
    asked = question_from(text)
    return (is_a_handoff(asked) and not asks_something(asked)
            and not has_a_known_intent(asked))


HANDOFFS = ["@mbubbleSearch here you go Kelly", "here you go", "there you go",
            "check this out", "here it is", "found it", "for you"]

STILL_ANSWERED = [
    "what did ansem say about zcash",
    "here is what i dont get about ethena",
    "check what banks said about robinhood",
    # A handoff phrase in front of a real question is still a question.
    "for you what is the best episode",
    "here you go, what did he say about solana",
]


def test_handoffs_are_skipped():
    for t in HANDOFFS:
        assert skipped(t), t


def test_questions_are_still_answered():
    for t in STILL_ANSWERED:
        assert not skipped(t), t

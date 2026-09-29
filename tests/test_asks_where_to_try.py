"""Asking where to try this is a request for a link, not an archive query.

Kelly Umerah asked "where do you send someone who wants to try it?" and the
bot searched the podcast for where to find a front door, then answered with
the SHOW's Discord at 3:45:54. He was asking where to use the search engine.
"""
from app.x_bot import _ASKS_WHAT_THIS_IS, about_answer

WANTS_THE_LINK = [
    "where do you send someone who wants to try it?",
    "where can i try it", "where's the link", "where is the demo",
    "how do i use this", "link?", "where can i find it",
]
# Real archive questions that happen to start with "where". The first
# version of this pattern swallowed them by accepting a bare "find".
IS_AN_ARCHIVE_QUESTION = [
    "where did ansem say that",
    "where can i find the ethena clip",
    "where can i find the part about zcash",
    "where do i find the mcg episode on pons",
    "what did banks say about robinhood",
]


def test_asking_where_to_try_it_is_recognised():
    for q in WANTS_THE_LINK:
        assert _ASKS_WHAT_THIS_IS.search(q), q


def test_archive_questions_are_left_alone():
    for q in IS_AN_ARCHIVE_QUESTION:
        assert not _ASKS_WHAT_THIS_IS.search(q), q


def test_the_answer_carries_the_site():
    out = about_answer("where can i try it", site="https://search.lexthedev.com")
    assert out and out.rstrip().endswith("https://search.lexthedev.com")

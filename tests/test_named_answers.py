"""Asked whether Ansem said Zcash could reach 10,000, the site said no.

Three separate things had to be fixed, each one enough on its own to lose
the answer, and each is held here:

  * Whisper wrote ZEC as "Zeke" (tests/test_names.py covers the spelling).
  * Two reply lines had no speaker label, so a question naming Ansem got
    his lines without the question he was agreeing to.
  * Routing picked ThreadGuy over Market Bubble by 0.03 for a question that
    named Ansem.
"""

from app.main import _prefer_named
from app.podcast import _questions_answered, _sectioned
from app.schemas import PodcastHit

EXCHANGE = """[26:06] Ansem: That's kind of where I'm at with like.
[26:08] rasmr: If that happens, then isn't it like ZEC to 10,000, Hype to 1,000, Pump to 20 billion?
[26:14] rasmr: Like, isn't that isn't that what you're saying?
[26:17] Ansem: Yeah, that is what I'm saying.
[26:20] Ansem: That's exactly what I'm saying."""


def hit(text: str) -> PodcastHit:
    return PodcastHit(episode_id="e", title="t", start_seconds=1, timestamp="26:06",
                      deep_link="u", score=1, text="x", text_ts=text)


def test_the_question_he_answered_comes_with_his_answer():
    body = _sectioned(hit(EXCHANGE), False, "Ansem")
    assert '<asked by="rasmr" answered-at="26:17">' in body
    assert "ZEC to 10,000" in body.split("</asked>")[0]
    said = body.split('<said-by name="Ansem">')[1]
    assert "ZEC to 10,000" not in said, "the question is never his words"
    assert "That's exactly what I'm saying" in said


def test_only_a_question_counts_not_any_line_before_his():
    statement = EXCHANGE.replace("what you're saying?", "what you're saying.")
    assert _questions_answered(statement, "Ansem") == []


def test_an_unidentified_voice_is_never_the_asker():
    unlabelled = """[1:00] Is this the top?
[1:05] Ansem: No."""
    assert _questions_answered(unlabelled, "Ansem") == []


def test_asking_about_the_asker_does_not_pull_in_the_answers():
    body = _sectioned(hit(EXCHANGE), False, "rasmr")
    assert "<asked" not in body and "exactly what I'm saying" not in body


def probe(key, score):
    return {"key": key, "label": key, "href": "/", "score": score, "hits": [1]}


def test_a_named_host_wins_a_near_tie():
    probes = [probe("threadguy", 0.535), probe("podcast", 0.502), probe("mcg", 0.3)]
    assert _prefer_named(probes, "what is ansem's price target for zcash")[0]["key"] \
        == "podcast"


def test_but_not_when_the_named_show_is_far_behind():
    probes = [probe("threadguy", 0.70), probe("podcast", 0.40)]
    assert _prefer_named(probes, "what did ansem say about zcash")[0]["key"] == "threadguy"


def test_two_hosts_named_leaves_the_order_alone():
    probes = [probe("threadguy", 0.55), probe("podcast", 0.50)]
    q = "what did ansem say on threadguy about zcash"
    assert _prefer_named(probes, q)[0]["key"] == "threadguy"


def test_no_host_named_leaves_the_order_alone():
    probes = [probe("threadguy", 0.55), probe("podcast", 0.54)]
    assert _prefer_named(probes, "who is bullish on zcash") == probes


def test_an_archive_named_in_the_question_wins_outright():
    """The hackathon demo: MCG had it at 6:48, the host rule sent it to
    Market Bubble because the question contains "ansem"."""
    probes = [probe("podcast", 0.62), probe("mcg", 0.48)]
    q = "what is the ansem hackathon prize pool and how is it split, in the MCG archive"
    assert _prefer_named(probes, q)[0]["key"] == "mcg"


def test_the_coin_and_the_hackathon_are_not_the_host():
    probes = [probe("mcg", 0.55), probe("podcast", 0.50)]
    for q in ("what is the ansem hackathon prize pool", "who is buying $ansem",
              "ansemhack judging dates", "ansem coin buybacks"):
        assert _prefer_named(probes, q)[0]["key"] == "mcg", q


def test_an_archive_with_nothing_does_not_win_by_being_named():
    empty = {"key": "mcg", "label": "mcg", "href": "/", "score": 0.0, "hits": []}
    probes = [probe("podcast", 0.6), empty]
    assert _prefer_named(probes, "anything on mcg about this")[0]["key"] == "podcast"

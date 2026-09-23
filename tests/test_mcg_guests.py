"""Who was in the room, and the two ways this got it wrong.

MCG cannot have speaker labels: one project per episode means almost every
guest appears once, so the recurrence trick that names Ansem and Banks has
nothing to cluster. What it can have is the weaker, honest fact -- who was
on this episode -- and the whole value of that depends on it never naming
somebody who was not there.

Both failures below were found on real episodes, not imagined. Each one
passed every check that existed at the time.
"""
import pytest

from scripts.index_mcg_guests import PRESENCE, keep, passages


def seg(t, text):
    return {"t": float(t), "text": text}


# The 20 Sep stream: a guest is asked about his third co-founder and names
# him. The name is real, spoken, at that second, and spelled correctly --
# and he is on a website, not on the show.
TALKED_ABOUT = [
    seg(1600, "And who is that, does that person have a name?"),
    seg(1605, "Yeah, of course he's on the website. His name is Micah Walter range."),
    seg(1610, "He was very happy to jump with us as a co-founder."),
    seg(1615, "He's a third co-founder with us."),
]

# The same stream, 56:10. First person, in the room, role stated himself.
SELF_INTRODUCED = [
    seg(3360, "If you guys would like to start us off, maybe Ferdy."),
    seg(3369, "Thanks for having us, firstly."),
    seg(3370, "So basically, I'm Ferdy, for the ones that don't know me."),
    seg(3376, "So I'm the accelerator lead of X Ventures, which is a private fund."),
]

ARRIVED = [
    seg(8, "Let's bring Ozzy up and chat with our guy at Pawns."),
    seg(14, "Ozzy, how you doing, welcome to the stream."),
]


def test_a_co_founder_described_in_the_third_person_is_not_a_guest():
    """The one that got through: every check passed and he was not there."""
    guest = {"name": "Micah Walter Range", "at_seconds": 1605,
             "evidence": "his name is Micah Walter range"}
    assert not keep(guest, TALKED_ABOUT)


def test_someone_who_introduces_himself_is_a_guest():
    guest = {"name": "Ferdy", "at_seconds": 3370,
             "evidence": "I'm Ferdy, for the ones that don't know me"}
    assert keep(guest, SELF_INTRODUCED)


def test_someone_brought_on_is_a_guest():
    guest = {"name": "Ozzy", "at_seconds": 8,
             "evidence": "Let's bring Ozzy up"}
    assert keep(guest, ARRIVED)


def test_evidence_from_elsewhere_in_the_episode_is_refused():
    """The first failure: one introduction's words, another guest's slot.

    The model returned "Furrow" at 28:54 carrying the quote that introduces
    micah at 61:39. Checking the quote existed *somewhere* passed it.
    """
    far_away = ARRIVED + [seg(3699, "we're gonna bring up micah, welcome to the stream")]
    guest = {"name": "Furrow", "at_seconds": 1734,
             "evidence": "we're gonna bring up micah"}
    assert not keep(guest, far_away)


def test_half_a_name_is_not_a_name():
    """A right first name must not carry a surname nobody said."""
    guest = {"name": "Ozzy Shevchenko", "at_seconds": 8,
             "evidence": "Let's bring Ozzy up"}
    assert not keep(guest, ARRIVED)


@pytest.mark.parametrize("guest", [
    {"name": "", "at_seconds": 8, "evidence": "Let's bring Ozzy up"},
    {"name": "Ozzy", "at_seconds": 8, "evidence": ""},
    {"name": "Ozzy", "at_seconds": None, "evidence": "Let's bring Ozzy up"},
    {"name": "Ozzy", "at_seconds": "soon", "evidence": "Let's bring Ozzy up"},
    {"name": "Ozzy", "at_seconds": 99999, "evidence": "Let's bring Ozzy up"},
])
def test_malformed_rows_are_refused_rather_than_raising(guest):
    assert not keep(guest, ARRIVED)


def test_third_person_is_never_presence():
    assert not PRESENCE.search("his name is micah walter range")
    assert not PRESENCE.search("he was very happy to jump with us as a co-founder")


def test_first_person_and_arrival_are_presence():
    assert PRESENCE.search("i'm the founder of route")
    assert PRESENCE.search("let's bring ozzy up")
    assert PRESENCE.search("welcome to the stream")


def test_passages_merge_overlapping_cues():
    """One introduction is one excerpt, not four overlapping ones."""
    segs = [seg(100, "let's bring him up"), seg(110, "welcome in"),
            seg(120, "introduce yourself")]
    assert len(passages(segs)) == 1


def test_an_episode_with_no_introduction_costs_nothing():
    """No cue means no model call at all, which is most of the saving."""
    assert passages([seg(10, "bitcoin is at eighty five thousand")]) == []

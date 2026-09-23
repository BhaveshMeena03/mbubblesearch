"""Reading an episode's people and its subject off what is known.

The rule the whole thing rests on: a guest is somebody the show introduced
out loud and the transcript backs up, a subject is what the title says the
episode is about, and the second never becomes the first. A title is free
to write and nobody said it on air.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app import mcg_guests


@pytest.fixture(autouse=True)
def _fresh():
    """load() is cached for the process; these tests move the file."""
    mcg_guests.load.cache_clear()
    yield
    mcg_guests.load.cache_clear()


# --- reading the subject off a title --------------------------------------

@pytest.mark.parametrize("title,want", [
    ("Hookr: Hook Launchpad. Build custom Uniswap Hooks", "Hookr"),
    ("Trojan: Solana's Fastest Trading Bot", "Trojan"),
    ("$REPPO: Network Of Data Marketplaces, V2 In 2 Days", "REPPO"),
    ("ICM.Run: Internet Capital Markets, Tokenization", "ICM.Run"),
    ("Scherwode.fun: The Launchpad Built for Robinhood Chain", "Scherwode.fun"),
    ("Dolphin | The AI Lab Turning Idle GPU's Into Revenue", "Dolphin"),
    # The project sits inside the parenthesis; the person sits outside it.
    ("Luca (Founder of JTVO) : From Startup to $7M", "JTVO"),
    # "<person> with <project>" puts the project on the left.
    ("DevFunPump with RxbiXyz: Innovating Launchpads", "DevFunPump"),
    # "<person> on <project>" puts it on the right.
    ("Gavin on Paragon: $PRGN Token Ecosystem", "Paragon"),
])
def test_titles_that_name_their_subject(title, want):
    assert mcg_guests.subject_from_title(title) == want


def test_the_channel_is_never_the_subject():
    """`on` points both ways, and one of them is the show itself.

    "$PONS on MCG" answered "MCG" while "Gavin on Paragon" answered
    "Paragon" -- the same rule, read the same way, and MCG is the channel
    all 645 episodes are on.
    """
    assert mcg_guests.subject_from_title(
        "$PONS on MCG | Discussing their beef with Uniswap") == "PONS"


@pytest.mark.parametrize("title", [
    "🔴 LIVE: Bitcoin Hits $85K | Market Update + Interviews",
    "🔴 LIVE NOW: DeFi Exploit, Onchain Cooked",
    "CRYPTO HEADED HIGHER JANUARY 2026? w/@swarms_corp on @MCGlive",
    "EQUITY ANCHOR, OWNERSHIP SZN w/@fairscalexyz @stardotfun",
    "",
])
def test_titles_that_name_no_single_subject(title):
    """A live market stream and a panel are not shows about one thing."""
    assert mcg_guests.subject_from_title(title) is None


def test_a_sentence_is_not_a_name():
    assert mcg_guests.subject_from_title(
        "Will Robinhood Szn Last And What Happens To The Trenches Next") is None


# --- the file ------------------------------------------------------------

def test_a_missing_index_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(mcg_guests, "_PATH", tmp_path / "gone.json")
    assert mcg_guests.load() == {}
    assert mcg_guests.for_episode("anything") == {"guests": [], "subject": None}


def test_a_corrupt_index_is_not_an_error(monkeypatch, tmp_path):
    bad = tmp_path / "mcg_guests.json"
    bad.write_text("{not json at all")
    monkeypatch.setattr(mcg_guests, "_PATH", bad)
    assert mcg_guests.load() == {}


def test_an_episode_carries_its_own_guests(monkeypatch, tmp_path):
    path = tmp_path / "mcg_guests.json"
    path.write_text(json.dumps({
        "vid1": {"title": "Hookr: Hook Launchpad", "url": "u",
                 "guests": [{"name": "Ozzy", "at_seconds": 8,
                             "evidence": "let's bring Ozzy up"}]},
    }))
    monkeypatch.setattr(mcg_guests, "_PATH", path)
    got = mcg_guests.for_episode("vid1", "Hookr: Hook Launchpad")
    assert [g["name"] for g in got["guests"]] == ["Ozzy"]
    assert got["subject"] == "Hookr"


def test_an_unknown_episode_gets_an_empty_list_not_a_guess(monkeypatch, tmp_path):
    path = tmp_path / "mcg_guests.json"
    path.write_text(json.dumps({}))
    monkeypatch.setattr(mcg_guests, "_PATH", path)
    got = mcg_guests.for_episode("missing", "Hookr: Hook Launchpad")
    assert got["guests"] == []
    assert got["subject"] == "Hookr", "the title still says what it is about"


# --- the route ------------------------------------------------------------

def test_the_episodes_route_carries_people_and_subjects():
    with TestClient(main_module.app) as c:
        rows = c.get("/v1/mcg/episodes").json()
    assert rows, "no MCG episodes served"
    assert all("guests" in r and "subject" in r for r in rows)

    named = [r for r in rows if r["guests"]]
    assert named, "no episode carries a guest"
    for row in named[:20]:
        for g in row["guests"]:
            assert g.get("name"), "a guest with no name"
            assert g.get("evidence"), "a guest with nothing backing it"


def test_the_real_index_never_claims_a_guest_it_cannot_quote():
    """Every published name has the words that put the person in the room."""
    for row in mcg_guests.load().values():
        for g in row["guests"]:
            assert g.get("evidence", "").strip(), (
                f"{g.get('name')} in {row['title'][:40]} has no evidence")
            assert isinstance(g.get("at_seconds"), (int, float))

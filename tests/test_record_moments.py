"""The four cards on The Record's page say what somebody said, at a second.

Each one is checked against the stored transcript: the words on the card
have to be the words at that second, in order, with nothing added but
punctuation. A card is the one place on the page a reader cannot check a
citation before trusting it, so this test does it for them.
"""

import gzip
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "demo" / "finance.html").read_text()
CARDS = re.findall(
    r'\{vid: "([\w-]{11})", at: (\d+), by: "([^"]+)",\s*q: "([^"]+)",\s*said: "([^"]+)"',
    PAGE)


def _shelf() -> dict:
    plain = ROOT / "data" / "tradfi_episodes.json"
    rows = (json.loads(plain.read_text()) if plain.exists() else
            json.loads(gzip.decompress((ROOT / "data" / "tradfi_episodes.json.gz").read_bytes())))
    return {r["episode_id"]: r for r in rows}


SHELF = _shelf()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+|\[expletive\]", text.lower().replace("’", "'"))


def test_there_are_four_cards():
    assert len(CARDS) == 4


@pytest.mark.parametrize("vid, at, by, q, said", CARDS)
def test_every_card_is_word_for_word_at_its_second(vid, at, by, q, said):
    episode = SHELF[vid]
    assert episode["subject"] == by
    segments = episode["segments"]
    at = int(at)
    start = max(i for i, s in enumerate(segments) if s["t"] <= at + 0.5)
    assert at - segments[start]["t"] < 6, "the second is not where a line starts"
    spoken = words(" ".join(s["text"] for s in segments[start:start + 6]))
    wanted = words(said)
    for begin in range(len(spoken)):
        run = spoken[begin:begin + len(wanted)]
        if len(run) == len(wanted) and all(w == "[expletive]" or w == s
                                           for w, s in zip(wanted, run, strict=True)):
            break
    else:
        pytest.fail(f"{by} at {at}: the card's words are not in the transcript there")


@pytest.mark.parametrize("vid, at, by, q, said", CARDS)
def test_every_card_has_its_frame(vid, at, by, q, said):
    frame = ROOT / "demo" / "moments" / f"{vid}-{at}.webp"
    assert frame.is_file(), frame
    assert frame.read_bytes()[8:12] == b"WEBP"


def test_the_record_says_it_is_ours():
    assert "gathered by us into one archive" in PAGE

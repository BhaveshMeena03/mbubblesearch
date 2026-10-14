"""The question cards on the channel pages (demo/moments.js).

A card says somebody said something at a second, with that second's
frame on it. For the two archives whose transcripts are in the repo,
Market Bubble's and the Musk interviews, every card is held to the
transcript word for word here, and every Elon card to the voice labels.
MCG's, ThreadGuy's and rasmr's transcripts live only in the index, so
their cards were checked there when they were chosen; here they get the
same checks on everything else.
"""

import gzip
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "demo" / "moments.js").read_text()
CARD = re.compile(
    r'\{vid: "([\w-]{11})", at: (\d+), who: "([^"]+)",\s*'
    r'q: "([^"]+)",\s*said: "([^"]+)",\s*where: "([^"]+)", when: "([^"]+)"\}')


def cards(archive: str) -> list[tuple]:
    block = SCRIPT.split(f"    {archive}: [")[1].split("\n    ]")[0]
    return CARD.findall(block)


CHANNELS = ("podcast", "mcg", "threadguy", "elon", "rasmr")
ALL = [(a, *c) for a in CHANNELS for c in cards(a)]


def shelf(name: str) -> dict:
    plain = ROOT / "data" / f"{name}.json"
    rows = (json.loads(plain.read_text()) if plain.exists() else
            json.loads(gzip.decompress((ROOT / "data" / f"{name}.json.gz").read_bytes())))
    return {str(r["episode_id"]): r for r in rows}


def words(text: str) -> list[str]:
    """Lower-case words. An apostrophe inside a word stays (it's, don't);
    one at either end is a quotation mark and goes."""
    found = re.findall(r"[a-z0-9']+|\[expletive\]",
                       text.lower().replace("’", "'").replace("$", "").replace(",", ""))
    return [w if w == "[expletive]" else w.strip("'") for w in found if w.strip("'")]


def said_there(segments: list[dict], at: int, said: str) -> int | None:
    """The line the card's words start in, if they are at that second."""
    start = max(i for i, s in enumerate(segments) if s["t"] <= at + 0.5)
    if at - segments[start]["t"] >= 8:
        return None
    spoken = []
    for i in range(start, min(start + 6, len(segments))):
        spoken += [(w, i) for w in words(segments[i]["text"])]
    wanted = words(said)
    for begin in range(len(spoken) - len(wanted) + 1):
        run = spoken[begin:begin + len(wanted)]
        if all(w == "[expletive]" or w == s for w, (s, _) in zip(wanted, run, strict=True)):
            return run[0][1]
    return None


def test_every_channel_has_cards_and_none_is_alone_on_a_row():
    for archive in CHANNELS:
        assert len(cards(archive)) in (3, 4), archive


@pytest.mark.parametrize("archive, vid, at, who, q, said, where, when", ALL)
def test_every_card_has_its_frame(archive, vid, at, who, q, said, where, when):
    frame = ROOT / "demo" / "moments" / f"{vid}-{at}.webp"
    assert frame.is_file(), frame
    assert frame.read_bytes()[8:12] == b"WEBP"
    assert frame.stat().st_size < 120_000


@pytest.mark.parametrize("archive, vid, at, who, q, said, where, when", ALL)
def test_every_card_reads_cleanly(archive, vid, at, who, q, said, where, when):
    for text in (who, q, said, where, when):
        assert "—" not in text and "–" not in text
    assert q.endswith("?")
    assert re.fullmatch(r"\d{1,2} [A-Z][a-z]{2} 20\d\d", when), when
    assert 6 <= len(words(said)) <= 60


PODCAST = shelf("episodes")
LABELS = json.loads((ROOT / "data" / "speaker_map.json").read_text())


@pytest.mark.parametrize("vid, at, who, q, said, where, when", cards("podcast"))
def test_every_market_bubble_card_is_word_for_word_at_its_second(vid, at, who, q, said, where, when):
    line = said_there(PODCAST[vid]["segments"], int(at), said)
    assert line is not None, f"{who} at {at}: not in the transcript there"
    # A host's line has to be in that host's voice. A guest's is unlabelled,
    # and was checked against the picture instead.
    label = LABELS.get(vid, {}).get(str(line))
    if who in ("Ansem", "FaZe Banks"):
        assert label == who, f"{who} at {at} is labelled {label}"
    else:
        assert label is None, f"{who} at {at} is labelled as host {label}"


ELON = shelf("elon_episodes")
ELON_LABELS = json.loads((ROOT / "data" / "elon_speaker_map.json").read_text())


@pytest.mark.parametrize("vid, at, who, q, said, where, when", cards("elon"))
def test_every_elon_card_is_word_for_word_and_in_his_voice(vid, at, who, q, said, where, when):
    segments = ELON[vid]["segments"]
    line = said_there(segments, int(at), said)
    assert line is not None, f"{at}: not in the transcript there"
    labelled = ELON_LABELS.get(vid, {}).get(str(line))
    # The Rogan #2281 line predates the labels on that episode; every other
    # card's first line must be labelled as his.
    if ELON_LABELS.get(vid):
        assert labelled == "Elon Musk", f"{vid} line {line} is {labelled}"


@pytest.mark.parametrize("page, archive", [("podcast.html", "podcast"), ("mcg.html", "mcg"),
                                           ("threadguy.html", "threadguy"), ("elon.html", "elon"),
                                           ("rasmr.html", "rasmr")])
def test_every_channel_page_carries_its_cards(page, archive):
    html = (ROOT / "demo" / page).read_text()
    assert f'data-moments="{archive}"' in html
    assert '<div class="mm-grid"></div>' in html and '<div class="mm-stage" hidden></div>' in html
    assert '<script src="/demo/moments.js" defer></script>' in html

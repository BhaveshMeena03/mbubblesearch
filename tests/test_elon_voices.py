"""Whose voice a line of the Musk archive is in.

The labels come from the sound of each recording (scripts/
label_elon_voices.py). These check the decisions that turn a fingerprint
into a name, on voices made up for the purpose: three directions in a
small space stand in for three people, which is all a decision about
"nearer to whom" needs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import label_elon_voices as voices  # noqa: E402
from app.podcast import ELON_SYSTEM_PROMPT  # noqa: E402

ELON, ROGAN, GUEST = np.eye(8)[0], np.eye(8)[1], np.eye(8)[2]
NAN = np.full(8, np.nan)


def near(voice: np.ndarray, seed: int, by: float = 0.12) -> np.ndarray:
    """The same voice on another line: close, never identical."""
    rng = np.random.default_rng(seed)
    return voices.unit(voice + by * rng.standard_normal(8))


def recording(channel: str, lines: list[tuple[np.ndarray, np.ndarray]],
              short: list[np.ndarray] = ()) -> dict:
    """`lines` is (start of the line, end of the line) for each long line;
    `short` is one hearing for each line too brief to stand alone."""
    count = len(lines)
    rec = {"id": channel, "title": channel, "channel": channel,
           "segments": [{"t": float(i), "text": "x"} for i in range(count + len(short))],
           "vectors": np.stack([a for a, _ in lines]),
           "tail": np.stack([b for _, b in lines]),
           "kept": list(range(count)),
           "short": (np.stack(list(short)) if len(short)
                     else np.zeros((0, 8))),
           "short_kept": list(range(count, count + len(short)))}
    rec["voices"] = {0: np.array([i for i, (a, _) in enumerate(lines) if a @ ELON > 0.7]),
                     1: np.array([i for i, (a, _) in enumerate(lines) if a @ ELON <= 0.7])}
    rec["big"] = {v: voices.centre(rec["vectors"], rows)
                  for v, rows in rec["voices"].items() if len(rows)}
    return rec


def talk(voice: np.ndarray, lines: int, seed: int) -> list:
    return [(near(voice, seed + i), NAN) for i in range(lines)]


# --- one line ----------------------------------------------------------------

def test_a_line_is_his_only_when_it_is_plainly_nearer_him():
    assert voices.decide(0.82, 0.05) == voices.ELON
    assert voices.decide(0.04, 0.77) == voices.OTHER
    # Near both, or near neither: no name.
    assert voices.decide(0.55, 0.50) is None
    assert voices.decide(0.20, 0.02) is None


def test_a_short_line_is_asked_for_more():
    """It was heard with a second of whoever spoke either side of it."""
    assert voices.decide(0.60, 0.42) == voices.ELON
    assert voices.decide(0.60, 0.42, voices.CLEAR_BY_SHORT) is None
    assert voices.decide(0.80, 0.10, voices.CLEAR_BY_SHORT) == voices.ELON


# --- one recording -----------------------------------------------------------

def test_a_line_that_starts_in_one_voice_and_ends_in_another_gets_no_name():
    """The first version listened to the start alone, and a whole cluster
    of his answers that began on the last word of the question came out
    as somebody else's. The dangerous one is the other way round."""
    lines = talk(ELON, 14, 1) + talk(ROGAN, 14, 50)
    lines.append((near(ELON, 90), near(ROGAN, 91)))      # his tail, then a question
    lines.append((near(ROGAN, 92), near(ELON, 93)))      # the question's tail, then him
    lines.append((near(ELON, 94), near(ELON, 95)))       # him at both ends
    rec = recording("show", lines)
    labels = voices.label(rec, ELON, {})
    assert "28" not in labels and "29" not in labels
    assert labels["30"] == voices.ELON
    assert labels["0"] == voices.ELON and labels["14"] == voices.OTHER


def test_short_lines_are_named_from_what_surrounds_them_or_not_at_all():
    blur = voices.unit(ELON + ROGAN)                     # a change of speaker
    rec = recording("show", talk(ELON, 14, 1) + talk(ROGAN, 14, 50),
                    short=[near(ELON, 7), near(ROGAN, 8), blur])
    labels = voices.label(rec, ELON, {})
    assert labels["28"] == voices.ELON
    assert labels["29"] == voices.OTHER
    assert "30" not in labels


def test_a_recording_where_his_voice_is_not_found_is_labelled_nowhere():
    """Every label there would be a guess."""
    rec = recording("show", talk(ROGAN, 14, 1) + talk(GUEST, 14, 50))
    assert voices.label(rec, ELON, {}) == {}


def test_a_voice_between_his_and_somebody_elses_is_neither():
    """Small clusters at 0.48 to 0.66 against his voice read, by their
    words, as him laughing, and one or two as anybody. They are not put
    on either side; their lines are heard one at a time."""
    assert voices.NOT_HIS < 0.48 and 0.66 < voices.HIS


# --- across recordings -------------------------------------------------------

def shows() -> list[dict]:
    made = []
    for n in range(4):
        made.append(recording("PowerfulJRE",
                              talk(ELON, 14, 10 * n) + talk(ROGAN, 14, 500 + 10 * n)))
    for n in range(3):
        made.append(recording("Somebody Else",
                              talk(ELON, 14, 900 + 10 * n)
                              + talk(np.eye(8)[4 + n], 14, 700 + 10 * n)))
    return made


def test_the_voice_in_every_recording_is_found_without_being_told(monkeypatch):
    """The host is in four of seven and louder in each. Only one voice is
    in all of them."""
    made = shows()
    monkeypatch.setattr(voices, "voices", lambda vectors: {
        0: np.flatnonzero(vectors @ ELON > 0.7),
        1: np.flatnonzero(vectors @ ELON <= 0.7)})
    found = voices.everywhere(made)
    assert float(found @ ELON) > 0.95
    assert float(found @ ROGAN) < 0.2


def test_a_host_is_named_by_recurring_and_a_one_off_interviewer_is_not(monkeypatch):
    made = shows()
    monkeypatch.setattr(voices, "voices", lambda vectors: {
        0: np.flatnonzero(vectors @ ELON > 0.7),
        1: np.flatnonzero(vectors @ ELON <= 0.7)})
    elon = voices.everywhere(made)
    rogan = voices.host_voice(made, "PowerfulJRE", elon)
    assert rogan is not None and float(rogan @ ROGAN) > 0.95
    # Three recordings on a channel, three different people asking.
    assert voices.host_voice(made, "Somebody Else", elon) is None
    labels = voices.label(made[0], elon, {"Joe Rogan": rogan})
    assert labels["14"] == "Joe Rogan"
    assert voices.label(made[5], elon, {"Joe Rogan": rogan})["14"] == voices.OTHER


# --- what the model is told --------------------------------------------------

def test_the_answer_rules_say_what_a_name_on_a_line_means():
    assert '"[16:16] Elon Musk: ..."' in ELON_SYSTEM_PROMPT
    assert '"Other speaker:", is NOT him' in ELON_SYSTEM_PROMPT
    assert "A line with no name was too short or too mixed to tell" in ELON_SYSTEM_PROMPT


def test_answers_written_before_the_names_are_not_served_again():
    source = (ROOT / "app" / "main.py").read_text()
    assert 'surface="elon-v2"' in source
    assert 'surface="elon-stream-v2"' in source
    assert 'surface="elon"' not in source


@pytest.mark.skipif(not (ROOT / "data" / "elon_speaker_map.json").exists(),
                    reason="the map is built from audio, on the laptop")
def test_the_shipped_map_names_him_in_every_recording():
    import gzip
    import json

    shelf = json.loads(gzip.decompress(
        (ROOT / "data" / "elon_episodes.json.gz").read_bytes()))
    names = json.loads((ROOT / "data" / "elon_speaker_map.json").read_text())
    for episode in shelf:
        said = names.get(episode["episode_id"], {})
        assert voices.ELON in said.values(), episode["title"]
        # Every index is a real line of that recording.
        assert all(0 <= int(i) < len(episode["segments"]) for i in said)


# --- the map against two things that are known -------------------------------

def _shelf_and_names():
    import gzip
    import json

    shelf = {e["episode_id"]: e for e in json.loads(gzip.decompress(
        (ROOT / "data" / "elon_episodes.json.gz").read_bytes()))}
    names = json.loads((ROOT / "data" / "elon_speaker_map.json").read_text())
    return shelf, names


@pytest.mark.skipif(not (ROOT / "data" / "elon_speaker_map.json").exists(),
                    reason="the map is built from audio, on the laptop")
def test_nobody_is_named_elon_after_he_has_left_the_room():
    """The Neuralink episode is eight and a half hours and he is in the
    first hour and a half: Lex says "Thanks for listening to this
    conversation with Elon Musk" at 88 minutes and the rest is four other
    people. Seven hours, 5,517 lines, and not one of them may be his. It is
    the nearest thing this archive has to an answer key for the mistake
    that matters, somebody else's words under his name."""
    shelf, names = _shelf_and_names()
    lines = shelf["Kbk9BiPhm7o"]["segments"]
    said = names["Kbk9BiPhm7o"]
    handover = next(i for i, s in enumerate(lines)
                    if "Thanks for listening to this conversation with Elon Musk" in s["text"])
    after = [said.get(str(i)) for i in range(handover + 1, len(lines))]
    assert len(after) > 5000
    assert voices.ELON not in after
    # And he is found before it, or the test above proves nothing.
    before = [said.get(str(i)) for i in range(handover)]
    assert before.count(voices.ELON) > 500


@pytest.mark.skipif(not (ROOT / "data" / "elon_speaker_map.json").exists(),
                    reason="the map is built from audio, on the laptop")
def test_the_introduction_lex_reads_is_never_his():
    shelf, names = _shelf_and_names()
    opened = 0
    for vid, episode in shelf.items():
        first = episode["segments"][0]["text"]
        if not first.startswith("The following is a conversation with Elon Musk"):
            continue
        opened += 1
        assert names[vid].get("0") in ("Lex Fridman", None), episode["title"]
    assert opened >= 4

"""Who a recording in The Record belongs to, and what is on its lines.

The archive is organised by person, so the name on a recording is a claim
about every sentence in it. Two ways that claim was false were found by
reading the shelf on 9 October 2026, and both are checked against the
shelf the server ships.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.provenance import strip_speaker_labels  # noqa: E402
from scripts.ingest_tradfi import retitled, someone_else  # noqa: E402

SHELF = json.loads(gzip.decompress(
    (ROOT / "data" / "tradfi_episodes.json.gz").read_bytes()))
BY_ID = {row["episode_id"]: row for row in SHELF}


def test_the_july_2026_press_conference_is_the_new_chairs():
    """Filed under Jerome Powell for ten weeks, because he had given the
    others. It opens "My second FOMC committee meeting as chairman" and
    the reporters say "Chairman Warsh"."""
    row = BY_ID["DLFXUkOc_7I"]
    assert row["subject"] == "Kevin Warsh"
    assert row["title"] == "Kevin Warsh: FOMC Press Conference, July 29, 2026"
    said = " ".join(s["text"] for s in row["segments"])
    assert "meeting as chairman" in said
    assert "Chairman Warsh" in said


def test_the_handover_is_filed_by_what_each_man_says():
    """April 2026 is Powell's, in his words, though a reporter asks about
    "incoming Chairman Warsh" in it. June is the first of Warsh's."""
    april = " ".join(s["text"] for s in BY_ID["UR2yFg--1jY"]["segments"])
    assert BY_ID["UR2yFg--1jY"]["subject"] == "Jerome Powell"
    assert "my last press conference as chair" in april
    june = " ".join(s["text"] for s in BY_ID["fR7oZvlk_eY"]["segments"])
    assert BY_ID["fR7oZvlk_eY"]["subject"] == "Kevin Warsh"
    assert "to be back at the Federal Reserve" in june


def test_nothing_on_the_shelf_is_a_speaker_label_nobody_said():
    """Whisper wrote "CHAIRMAN BERNANKE." twelve times into a Powell
    press conference. An ingest that lets one through fails here."""
    for row in SHELF:
        _, labels = strip_speaker_labels(row["segments"])
        assert labels == [], f"{row['title']}: {labels}"


def test_a_corrected_subject_replaces_the_name_in_the_title():
    assert retitled("Jerome Powell: FOMC Press Conference, July 29, 2026",
                    "Jerome Powell", "Kevin Warsh") == (
        "Kevin Warsh: FOMC Press Conference, July 29, 2026")


def test_a_title_that_already_names_the_person_is_left_alone():
    title = "Legends Live @Citi, featuring Larry Fink and Leon Kalvaria"
    assert retitled(title, "Larry Fink", "Larry Fink") == title
    kept = "Jerome Powell: FOMC Press Conference, October 29, 2025"
    assert retitled(kept, "Jerome Powell", "Jerome Powell") == kept


# --- who the room says is speaking -------------------------------------------



def test_the_room_naming_another_chair_stops_the_ingest():
    """The real one: shelved as Powell's, and the words say otherwise."""
    row = BY_ID["DLFXUkOc_7I"]
    assert someone_else(row["segments"], "Jerome Powell").startswith("Warsh")
    assert someone_else(row["segments"], "Kevin Warsh") is None


def test_no_recording_on_the_shelf_is_addressed_to_somebody_else():
    """Every one, so the next office to change hands is caught by the
    shelf's own test and not by a reader."""
    for row in SHELF:
        assert someone_else(row["segments"], row["subject"]) is None, row["title"]


def test_one_passing_mention_is_not_the_room_addressing_somebody():
    said = [{"t": 0.0, "text": "I spoke to Chair Powell about this last week."},
            {"t": 8.0, "text": "And the chairman and CEO of the bank agreed."}]
    assert someone_else(said, "Jamie Dimon") is None

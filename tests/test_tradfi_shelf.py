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
from scripts.ingest_tradfi import retitled  # noqa: E402

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


def test_no_recording_filed_under_powell_is_addressed_to_somebody_else():
    for row in SHELF:
        if row.get("subject") != "Jerome Powell":
            continue
        said = " ".join(s["text"] for s in row["segments"])
        assert "Chairman Warsh" not in said, row["title"]
        assert "Chair Warsh" not in said, row["title"]


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

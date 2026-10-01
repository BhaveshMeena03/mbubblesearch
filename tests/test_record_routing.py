"""Questions about The Record's people reach The Record, on the server.

Two failures, found before inviting people to "ask me anything from the
record" under a CZ post:

- The bot read the plain shelf, data/tradfi_episodes.json, which the image
  does not ship (only the .gz is copied in). The name set was empty in
  production, so no finance name routed there at all.
- "cz" is two letters and the length rule dropped it, so CZ's interviews
  were unreachable by his name or his handle.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.x_bot as x  # noqa: E402


def test_the_names_load_from_the_gzipped_shelf_the_image_ships(tmp_path):
    shipped = tmp_path / "tradfi_episodes.json.gz"
    shutil.copy(ROOT / "data" / "tradfi_episodes.json.gz", shipped)
    names = x._tradfi_subjects(tmp_path / "tradfi_episodes.json")
    assert {"fink", "dalio", "cz"} <= names


def test_the_shipped_shelf_has_cz_in_it():
    rows = json.loads(gzip.decompress((ROOT / "data" / "tradfi_episodes.json.gz").read_bytes()))
    rows = rows if isinstance(rows, list) else list(rows.values())
    assert sum(1 for r in rows if r.get("subject") == "CZ") == 5


def test_cz_by_name_handle_or_full_name_goes_to_the_record():
    for q in ("@mbubbleSearch what did cz say about the super cycle",
              "@mbubbleSearch what did @cz_binance say about prison",
              "@mbubbleSearch what did changpeng zhao say about bnb"):
        assert x.corpus_for(q) == "tradfi", q


def test_the_show_still_wins_when_it_is_named_first():
    assert x.corpus_for("@mbubbleSearch what did ansem say about cz") == "podcast"

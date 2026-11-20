"""The explainer page says what the system does today.

It described Market Bubble alone, 18 shows and 66 hours, two months after
there were six archives and two thousand hours. Nothing on it is now a
count somebody has to retype: figures are floors held against the files
that ship, and settings are held against the settings.
"""

import gzip
import json
import re
from pathlib import Path

from app import main
from app.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "demo" / "how-it-works.html").read_text()
BODY = PAGE.split("</style>", 1)[1].split("<script>", 1)[0]
TEXT = " ".join(re.sub(r"<[^>]+>", " ", BODY).split())


def number(pattern: str) -> int:
    return int(re.search(pattern, TEXT, re.I).group(1).replace(",", ""))


def test_the_figures_it_opens_with_are_under_what_the_archives_hold():
    """Three of the six archives ship as plain lists, and they alone
    cover both floors. The page swaps in today's totals when it loads."""
    rows = [e for load in (main._mcg_episodes, main._threadguy_episodes, main._rasmr_episodes)
            for e in load()]
    hours = sum(float(e.get("seconds") or 0) for e in rows) / 3600
    assert number(r"more than ([\d,]+) hours") <= hours
    assert number(r"more than ([\d,]+) recordings") <= len(rows)
    assert 'id="hw-hours"' in PAGE and 'id="hw-recs"' in PAGE
    assert 'fetch("/v1/archives")' in PAGE


def test_the_exact_words_it_claims_are_in_the_term_indexes():
    held = 0
    for path in sorted((ROOT / "data").glob("term*.json*")):
        raw = path.read_bytes()
        found = json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)
        held += len(found["terms"])
    assert number(r"more than ([\d,]+) names and numbers") <= held


def test_every_setting_it_names_is_the_setting():
    s = get_settings()
    assert f"{s.chunk_max_chars:,}" in TEXT and f"{s.chunk_overlap_chars}" in TEXT
    assert f"{s.embedding_dimension:,}-dimension" in TEXT
    assert f"{s.rerank_candidates} candidates" in TEXT
    for name in (s.voyage_model, s.rerank_model, s.search_model):
        assert name in TEXT, name


def test_no_figure_is_typed_in_as_an_exact_count():
    for stale in ("18 shows", "38 files", "66 hours", "68,525", "3,107", "19,449",
                  "12 candidates"):
        assert stale not in TEXT, stale
    counts = re.findall(r"(more than )?([\d,]+) (hours|recordings|names and numbers)", TEXT, re.I)
    assert len(counts) == 3 and all(lead for lead, _, _ in counts)


def test_it_names_all_six_archives():
    for name in ("Market Bubble", "MCG Live", "ThreadGuy", "rasmr", "Elon Musk", "The Record"):
        assert name in TEXT, name


def test_the_recording_script_still_finds_its_seven_parts():
    """scripts/make_tech_demo.mjs reveals these by id."""
    script = (ROOT / "scripts" / "make_tech_demo.mjs").read_text()
    for part in ("s1", "s2", "s3", "s4", "s5", "s6", "rule"):
        assert f'id="{part}"' in PAGE and f'"{part}"' in script, part
    assert 'has("staged")' in PAGE
    # The links onward are for a reader, and are not in the film.
    assert ".onward{display:none" in PAGE and 'class="onward" id="onward"' in PAGE


def test_it_reads_on_a_phone_and_leads_somewhere():
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in PAGE
    assert "@media(max-width:700px)" in PAGE
    for link in ('href="/home"', 'href="/episodes"', 'href="/calls"'):
        assert link in BODY, link

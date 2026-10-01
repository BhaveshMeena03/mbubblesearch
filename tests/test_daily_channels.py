"""The morning job that indexes MCG Live and ThreadGuy on the laptop."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts" / "daily_channels_check.sh").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "sync-episodes.yml").read_text()


def test_it_covers_both_channels_and_transcribes_with_groq():
    assert "for archive in threadguy mcg" in SCRIPT
    assert "--transcriber groq" in SCRIPT


def test_it_commits_only_the_two_shelves():
    """It runs in a checkout that may have someone's work in it."""
    assert 'shelves=(data/threadguy_index.json data/mcg_index.json "${terms[@]}")' in SCRIPT
    assert "terms=(data/terms_threadguy.json.gz data/terms_mcg.json.gz)" in SCRIPT


def test_the_exact_word_indexes_are_rebuilt_weekly():
    assert '[ "$(date +%u)" = "7" ]' in SCRIPT
    assert 'scripts/build_term_index.py --archive "$archive"' in SCRIPT
    assert '-- "${shelves[@]}"' in SCRIPT
    assert "git add" not in SCRIPT


def test_it_never_bills_anthropic_directly_and_never_runs_twice():
    assert "unset ANTHROPIC_BASE_URL" in SCRIPT
    assert 'pgrep -f "scripts/ingest_mcg.py"' in SCRIPT


def test_github_warns_when_threadguy_is_behind():
    assert "--archive threadguy --list" in WORKFLOW

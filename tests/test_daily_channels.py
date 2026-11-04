"""The morning job that indexes MCG Live, ThreadGuy and rasmr on the laptop."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts" / "daily_channels_check.sh").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "sync-episodes.yml").read_text()


def test_it_covers_every_channel_and_transcribes_with_groq():
    assert "for archive in threadguy mcg rasmr; do" in SCRIPT
    assert "--transcriber groq" in SCRIPT


def test_it_commits_only_the_shelves():
    """It runs in a checkout that may have someone's work in it."""
    assert 'shelves=(data/threadguy_index.json data/mcg_index.json data/rasmr_index.json\n' \
           '         data/threadguy_summaries.json.gz "${terms[@]}"\n' \
           '         data/mcg_assets.json data/threadguy_assets.json)' in SCRIPT
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


def test_new_threadguy_uploads_get_notes_through_the_proxy():
    """After the ingest, so the newest episodes are on the shelf, and
    after the unset, so the model call cannot go to Anthropic directly."""
    notes = SCRIPT.index("scripts/summarize_threadguy.py --latest 10")
    assert SCRIPT.index("unset ANTHROPIC_BASE_URL") < notes
    assert SCRIPT.index("for archive in threadguy mcg") < notes


def test_the_groq_key_is_found_without_the_shell():
    """launchd gives the job an empty environment; the key is in .env."""
    ingest = (ROOT / "scripts" / "ingest_mcg.py").read_text()
    assert "get_settings().groq_api_key" in ingest


def test_new_uploads_reach_the_token_dashboards_gently():
    """The proxy is shared with the site and the bot: 60 calls in flight
    got rate limited, which slows real visitors."""
    assert "for archive in threadguy mcg; do\n  echo \"-- $archive tokens\"" in SCRIPT
    assert '--archive "$archive" --episodes 10 --store --workers 2' in SCRIPT
    assert "EXTRACT_CONCURRENCY=4" in SCRIPT


def test_the_jobs_can_find_ffmpeg_under_launchd():
    """launchd's PATH has no Homebrew; every download failed on 2026-10-02."""
    for name in ("daily_channels_check.sh", "daily_broadcast_check.sh"):
        script = (ROOT / "scripts" / name).read_text()
        assert 'export PATH="/opt/homebrew/bin:' in script, name


def test_a_stream_indexed_from_x_is_not_fetched_again_from_youtube():
    """rasmr's 12 Oct stream is age-gated on YouTube and went in from X.
    Its row names the YouTube id, so the morning run neither fails on it
    daily nor, once the gate lifts, indexes the same stream twice."""
    import json
    rows = json.loads((ROOT / "data" / "rasmr_index.json").read_text())
    ids = {r["id"] for r in rows}
    twins = [r for r in rows if r.get("youtube")]
    assert twins
    for row in twins:
        assert row.get("source") == "x" and row["youtube"] not in ids, row["id"]


def test_market_bubble_summaries_are_refreshed_and_committed():
    """The site serves them from the file, so the file has to keep up."""
    packed = SCRIPT.index("scripts/pack_summaries.py")
    assert SCRIPT.index("unset ANTHROPIC_BASE_URL") < packed
    assert packed < SCRIPT.index('git diff --quiet -- "${shelves[@]}"')
    assert "shelves+=(data/summaries.json.gz)" in SCRIPT


def test_threadguys_x_broadcasts_are_paired_every_morning_and_committed():
    """The extension's button on his posts needs each new broadcast paired;
    after the notes, so a show paired today already has its summary."""
    paired = SCRIPT.index("scripts/pair_threadguy_broadcasts.py --search")
    assert SCRIPT.index("scripts/summarize_threadguy.py") < paired
    assert paired < SCRIPT.index('git diff --quiet -- "${shelves[@]}"')
    for kept in ("data/threadguy_broadcast_links.json", "data/threadguy_x_broadcasts.json"):
        assert kept in SCRIPT


def test_mcg_gets_its_notes_every_morning_through_the_proxy_and_committed():
    notes = SCRIPT.index("scripts/summarize_mcg.py --latest 5")
    assert SCRIPT.index("unset ANTHROPIC_BASE_URL") < notes
    assert notes < SCRIPT.index('git diff --quiet -- "${shelves[@]}"')
    assert "data/mcg_summaries.json.gz" in SCRIPT


def test_new_clips_on_x_are_placed_every_morning_and_committed():
    placed = SCRIPT.index("scripts/place_x_clips.py --search")
    assert placed < SCRIPT.index('git diff --quiet -- "${shelves[@]}"')
    for kept in ("data/x_clips.json", "data/x_clips_search.json"):
        assert kept in SCRIPT


def test_our_link_posts_are_read_every_morning_and_committed():
    read = SCRIPT.index("scripts/place_x_links.py --search")
    assert read < SCRIPT.index('git diff --quiet -- "${shelves[@]}"')
    for kept in ("data/x_links.json", "data/x_links_search.json"):
        assert kept in SCRIPT


def test_market_bubble_and_ansem_recordings_are_indexed_before_the_clips():
    """Every new recording of theirs gets the extension's button the next
    morning, and a clip of it is placed the same run."""
    indexed = SCRIPT.index("scripts/add_x_recordings.py --search")
    assert indexed < SCRIPT.index("scripts/place_x_clips.py --search")
    assert indexed < SCRIPT.index("scripts/pack_summaries.py")
    for kept in ("data/x_recordings.json", "data/x_copies.json", "data/episodes.json.gz", "data/broadcast_links.json",
                 "data/term_index.json", "data/highlights.json"):
        assert kept in SCRIPT


def test_the_speaker_map_is_never_committed_unattended():
    """Rebuilding it moved 49 other episodes' labels when Episode 24 went in."""
    code = [line for line in SCRIPT.splitlines() if not line.lstrip().startswith("#")]
    assert not any("speaker_map" in line for line in code)


def test_rasmrs_x_broadcasts_are_paired_every_morning_and_committed():
    paired = SCRIPT.index("scripts/pair_rasmr_broadcasts.py --search")
    assert SCRIPT.index("for archive in threadguy mcg rasmr; do") < paired
    assert paired < SCRIPT.index("scripts/place_x_clips.py --search")
    for kept in ("data/rasmr_broadcast_links.json", "data/rasmr_x_broadcasts.json"):
        assert kept in SCRIPT

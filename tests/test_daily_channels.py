"""The morning job that indexes MCG Live, ThreadGuy and rasmr on the laptop."""

import contextlib
import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert "for archive in threadguy mcg; do\n    echo \"-- $archive tokens\"" in SCRIPT
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


PATH_LINE = 'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"'
STUBS = {
    "curl": "#!/bin/bash\nexit 0\n",
    "pgrep": "#!/bin/bash\nexit 1\n",
    "caffeinate": '#!/bin/bash\nwhile [[ $1 == -* ]]; do shift; done; exec "$@"\n',
    "git": '#!/bin/bash\necho "git $*" >> "$CALLS"; [[ $1 == diff ]] && exit 0; exit 1\n',
}
# lockf(1) as on macOS, for CI: flock the file, wait up to -t seconds
# (cut to half a second here), exit 75 if it stays held
LOCKF = f"""#!{sys.executable}
import fcntl, subprocess, sys, time
args, wait = sys.argv[1:], 0.0
while args[0].startswith("-"):
    flag = args.pop(0)
    if flag == "-t":
        wait = float(args.pop(0))
open(args[0] + ".waits", "a").write(f"{{wait:g}}\\n")
lock, end = open(args[0], "a"), time.time() + min(wait, 0.5)
while True:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except BlockingIOError:
        if time.time() >= end:
            sys.exit(75)
        time.sleep(0.05)
sys.exit(subprocess.run(args[1:]).returncode)
"""
X_STEPS = ["scripts/add_x_recordings.py --search", "scripts/place_x_clips.py --search",
           "scripts/place_x_links.py --search", "scripts/pack_summaries.py"]


@contextlib.contextmanager
def lock_held(path):
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def run_job(root, flag, real_lockf=False):
    """Run the job with every command stubbed; returns (exit, output, python calls)."""
    (root / "scripts").mkdir(exist_ok=True)
    assert PATH_LINE in SCRIPT
    # the job puts Homebrew first, which would hide the stubs on a Mac
    (root / "scripts" / "daily_channels_check.sh").write_text(SCRIPT.replace(PATH_LINE, ""))
    venv, stubs = root / ".venv" / "bin", root / "stubs"
    venv.mkdir(parents=True, exist_ok=True)
    stubs.mkdir(exist_ok=True)
    files = {**STUBS, "python": '#!/bin/bash\necho "python $*" >> "$CALLS"\n'}
    if not real_lockf:
        files["lockf"] = LOCKF
    for name, body in files.items():
        target = venv / name if name == "python" else stubs / name
        target.write_text(body)
        target.chmod(0o755)
    calls = root / "calls.log"
    calls.unlink(missing_ok=True)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "CALLS": str(calls), "HOME": str(root)}
    done = subprocess.run(["bash", str(root / "scripts" / "daily_channels_check.sh"), flag],
                          capture_output=True, text=True, env=env, timeout=60)
    lines = calls.read_text().splitlines() if calls.exists() else []
    return done.returncode, done.stdout + done.stderr, [ln[len("python -u "):] for ln in lines
                                                         if ln.startswith("python")]


def waits(root):
    return (root / ".channels-check.lock.waits").read_text().split()


def test_the_evening_run_does_only_the_x_steps(tmp_path):
    code, out, calls = run_job(tmp_path, "--run-x")
    assert code == 0, out
    assert calls == X_STEPS
    assert waits(tmp_path) == ["0"]


def test_the_morning_run_does_everything_including_the_x_steps(tmp_path):
    code, out, calls = run_job(tmp_path, "--run")
    assert code == 0, out
    steps = [c.split(" ")[0] for c in calls]
    for step in X_STEPS:
        assert step in calls
    assert steps.index("scripts/ingest_mcg.py") < steps.index("scripts/add_x_recordings.py")
    assert steps.index("scripts/pack_summaries.py") < steps.index("scripts/summarize_mcg.py")
    assert len(calls) > len(X_STEPS) + 5
    assert waits(tmp_path) == ["10800"]


def test_the_evening_run_skips_while_another_run_holds_the_lock(tmp_path):
    with lock_held(tmp_path / ".channels-check.lock"):
        code, out, calls = run_job(tmp_path, "--run-x")
    assert code == 0 and "another run held the lock" in out
    assert calls == []


def test_the_morning_run_gives_up_if_the_lock_stays_held(tmp_path):
    with lock_held(tmp_path / ".channels-check.lock"):
        code, out, calls = run_job(tmp_path, "--run")
    assert code == 1 and "another run held the lock" in out
    assert calls == []


@pytest.mark.skipif(not os.path.exists("/usr/bin/lockf"), reason="macOS lockf only")
def test_the_real_lockf_skips_when_held_and_frees_the_lock_after_a_run(tmp_path):
    with lock_held(tmp_path / ".channels-check.lock"):
        code, out, calls = run_job(tmp_path, "--run-x", real_lockf=True)
    assert code == 0 and "another run held the lock" in out and calls == []
    for _ in range(2):                   # the first run's lock is gone when it ends
        code, out, calls = run_job(tmp_path, "--run-x", real_lockf=True)
        assert code == 0, out
        assert calls == X_STEPS


def test_install_adds_the_evening_agent():
    assert 'write_plist "$PLIST" "$LABEL" --run 9' in SCRIPT
    assert 'write_plist "$X_PLIST" "$X_LABEL" --run-x 23' in SCRIPT


def test_the_script_parses():
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "daily_channels_check.sh")]).returncode == 0

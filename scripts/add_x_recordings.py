"""Index what Market Bubble and Ansem post on X that the archive doesn't
hold yet, so every new show and every one of his videos gets the
extension's button without anybody remembering to.

    .venv/bin/python scripts/add_x_recordings.py --search
    .venv/bin/python scripts/add_x_recordings.py POST_ID [POST_ID ...]
    .venv/bin/python scripts/add_x_recordings.py --search --dry-run

--search is what the morning job runs, before the clips, so a clip of a
recording indexed here finds it the same morning: one X search for the two
accounts' videos and broadcasts since the newest one read (about half a
cent a post, a few a day). What happened to each post is kept in
data/x_recordings.json.

For each post:

  1. a clip, ten minutes or less, is place_x_clips.py's, and so is a cut
     of a show the archive already holds, at any length: on 3 Nov 2026
     Ansem posted 34 minutes of Episode 24 as "Market Bubble ep.25".
  2. a broadcast still on air waits for the next run, and so does the
     Thursday show while the Friday watcher (daily_broadcast_check.sh) is
     still working on it.
  3. Ansem's broadcast of a Market Bubble episode is a copy, not a show:
     he goes live with every episode from his own account the moment
     Market Bubble does, under a broadcast id of its own. It is heard
     against the episode the archive holds (scripts/same_feed.py) and the
     clock between them goes in data/x_copies.json, so it gets the
     episode's panel. Until Market Bubble's own is indexed, it waits.
  4. anything else is indexed with add_broadcast.py on Groq. Ansem's videos
     go in as "Ansem on X", one voice, the way his 28 to 30 Oct videos did.
     Market Bubble's go in as episodes, with --no-labels: the speaker map
     is rebuilt whole for every episode, which moved 49 other episodes'
     labels when Episode 24 went in, so labels stay a hand step.

A post that fails is tried again on the next two runs, then left with its
reason for a person.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import episode_store  # noqa: E402
from scripts import place_x_clips as clips  # noqa: E402
from scripts import same_feed  # noqa: E402

STATE = ROOT / "data" / "x_recordings.json"
COPIES = ROOT / "data" / "x_copies.json"
POST_ID = re.compile(r"^[0-9]{5,25}$")

# Lowercase: yt-dlp hands back whichever case the account chose.
MARKET_BUBBLE, ANSEM = "marketbubble", "blknoiz06"
ACCOUNTS = (MARKET_BUBBLE, ANSEM)
QUERY = ('(from:MarketBubble OR from:blknoiz06) (has:videos OR url:"i/broadcasts") '
         '-is:retweet -is:reply')

# Up to this long it is a clip, whatever it is from.
CLIP_SECONDS = 600
MAX_TRIES = 3
ON_AIR = ("is_live", "is_upcoming", "post_live")
# Ansem's broadcast and Market Bubble's go up together: 23 episodes since
# May 2026 within 40 seconds of each other, most within one.
TWIN_SECONDS = 120


def posted_at(post: str) -> float:
    """When a post went up, in seconds: X ids carry their own time."""
    return ((int(post) >> 22) + clips.X_EPOCH_MS) / 1000


def twin_of(post: str, shows: set[str]) -> str | None:
    """The Market Bubble broadcast this post went up with, or None."""
    near = [s for s in shows if s != post and abs(posted_at(s) - posted_at(post)) <= TWIN_SECONDS]
    return min(near, key=lambda s: abs(posted_at(s) - posted_at(post)), default=None)


def market_bubble_shows(episodes: list[dict], state: dict) -> set[str]:
    """Market Bubble's broadcasts: the episodes held from its own posts,
    and the ones this script has seen but not indexed yet."""
    held = {e["episode_id"][2:] for e in episodes
            if e["episode_id"].startswith("x-")
            and f"/{MARKET_BUBBLE}/status/" in str(e.get("url", "")).lower()}
    seen = {p for p, row in state["posts"].items()
            if str(row.get("account", "")).lower() == MARKET_BUBBLE and row.get("what") != "leave"}
    return held | seen


def verdict(post: str, got: dict, held: set[str], cut: dict | None,
            on_air: bool, watcher: bool, twin: str | None = None,
            copies: dict | None = None) -> tuple[str, str]:
    """(what to do, why). "index", "copy", "wait" (try next run), or "leave"."""
    if got.get("error"):
        return "wait", got["error"]
    account = str(got.get("account") or "").lower()
    if account not in ACCOUNTS:
        return "leave", f"posted by @{got.get('account') or '?'}"
    if f"x-{post}" in held:
        return "leave", "already indexed"
    if post in (copies or {}):
        return "leave", f"already a copy of {copies[post].get('episode_id')}"
    if twin and got.get("broadcast") and account == ANSEM:
        if f"x-{twin}" not in held:
            return "wait", f"a copy of Market Bubble's {twin}, which is not indexed yet"
        if on_air:
            return "wait", "still on air"
        return "copy", f"a copy of x-{twin}"
    if not got.get("broadcast"):
        if not got.get("duration"):
            return "wait", "no length given"
        if got["duration"] <= CLIP_SECONDS:
            return "leave", "a clip, placed by place_x_clips.py"
    if cut:
        return "leave", f"a cut of {cut['episode_id']}, placed as a clip"
    if on_air:
        return "wait", "still on air"
    if watcher and account == "marketbubble":
        return "wait", "the Friday watcher is running"
    return "index", "not in the archive"


def command(post: str, got: dict) -> list[str]:
    """add_broadcast.py for this post."""
    url = f"https://x.com/{got['account']}/status/{post}"
    cmd = [sys.executable, "scripts/add_broadcast.py", url, "--engine", "groq"]
    if str(got["account"]).lower() == "blknoiz06":
        return cmd + ["--show", "Ansem on X"]
    # Off the air (checked first), so the length guard, which is there for a
    # show still streaming, has nothing to protect; and a full conversation
    # Market Bubble uploads runs well under its 1.25 hours.
    return cmd + ["--force", "--no-labels"]


def live_status(post: str) -> str:
    """yt-dlp's word for it: is_live, post_live, was_live, not_live, or ""."""
    try:
        run = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "yt-dlp"), "--socket-timeout", "30", "--no-warnings",
             "--playlist-items", "1", "--skip-download", "--print", "%(live_status)s",
             f"https://x.com/i/status/{post}"],
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        # Unknown is not "ended": a broadcast that won't say waits a run.
        return "is_live"
    return (run.stdout.strip().splitlines() or [""])[-1] if run.returncode == 0 else ""


def watcher_running() -> bool:
    return subprocess.run(["pgrep", "-f", "watch_for_broadcast.py"],
                          capture_output=True).returncode == 0


def cut_of(post: str, got: dict, episodes, workdir: Path) -> dict | None:
    """The show already held that this upload is a stretch of, or None. A
    broadcast is never a cut: it is the show, and transcribe_x_broadcast.py
    already refuses one that overlaps a recording it holds."""
    if got.get("broadcast"):
        return None
    if not got.get("caption"):
        got["caption"] = clips.listen(post, workdir, got.get("duration"))
    if not got["caption"]:
        return None
    pool = episodes() if callable(episodes) else episodes
    return clips.choose(clips.where_from(got["caption"], pool, got.get("duration") or 0)[0])


def load_state() -> dict:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state.setdefault("posts", {})
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("posts", nargs="*", help="X post ids")
    ap.add_argument("--search", action="store_true",
                    help="find the two accounts' new videos and broadcasts on X first")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, index nothing")
    args = ap.parse_args()
    bad = [p for p in args.posts if not POST_ID.match(p)]
    if bad:
        sys.exit(f"not post ids: {bad[:5]}")

    state = load_state()
    posts = list(args.posts)
    found: list[str] = []
    if args.search:
        import asyncio

        from app.config import redact
        try:
            found = asyncio.run(clips.search(state.get("newest"), QUERY))
        except Exception as exc:                                # noqa: BLE001
            sys.exit(f"could not ask X: {type(exc).__name__}: {redact(str(exc))[:200]}")
        print(f"{len(found)} new posts from @MarketBubble and @blknoiz06")
        # Those that waited last time, first: oldest first throughout.
        posts += [p for p, row in state["posts"].items()
                  if row.get("what") == "wait" and row.get("tries", 0) < MAX_TRIES]
        posts += found

    episodes = [dict(e, archive="podcast") for e in episode_store.load()]
    held = {e["episode_id"] for e in episodes}
    copies = json.loads(COPIES.read_text()) if COPIES.exists() else {}
    shows = clips.Shows()
    watcher = watcher_running()
    indexed = 0
    fetched: dict[str, dict] = {}

    def handle(post: str, workdir: Path, last_pass: bool) -> bool:
        """One post. False for a broadcast of Ansem's on the first pass: it
        may be a copy of a Market Bubble post this same run has yet to
        read, and his id is the smaller of the two about half the time."""
        nonlocal held, indexed
        if post not in fetched:
            fetched[post] = clips.fetch(post, workdir)
        got = fetched[post]
        if (not last_pass and got.get("broadcast")
                and str(got.get("account") or "").lower() == ANSEM):
            return False
        on_air = bool(got.get("broadcast")) and live_status(post) in ON_AIR
        cut = None
        if (str(got.get("account") or "").lower() in ACCOUNTS and f"x-{post}" not in held
                and not got.get("error") and (got.get("duration") or 0) > CLIP_SECONDS):
            cut = cut_of(post, got, lambda post=post: episodes + shows.before(
                clips.posted_on(post)), workdir)
        twin = twin_of(post, market_bubble_shows(episodes, state))
        what, why = verdict(post, got, held, cut, on_air, watcher, twin, copies)
        row = state["posts"].get(post, {})
        if what == "index" and not args.dry_run:
            print(f"{post}  indexing: {' '.join(command(post, got)[2:])}", flush=True)
            ran = subprocess.run(command(post, got), cwd=ROOT)
            now = episode_store.load()
            if any(e["episode_id"] == f"x-{post}" for e in now):
                what, why = "indexed", "indexed"
                indexed += 1
            elif ran.returncode == 0:
                what, why = "leave", "the transcriber held it already, as another copy"
            else:
                what, why = "wait", f"add_broadcast.py exited {ran.returncode}"
            episodes[:] = [dict(e, archive="podcast") for e in now]
            held = {e["episode_id"] for e in episodes}
        if what == "copy" and not args.dry_run:
            print(f"{post}  hearing it against x-{twin}", flush=True)
            measured = same_feed.measure(post, twin)
            if measured:
                copies[post] = {"account": got["account"], "episode_id": f"x-{twin}",
                                **measured, "measured": datetime.now(UTC).strftime("%Y-%m-%d")}
                COPIES.write_text(json.dumps(dict(sorted(copies.items())), indent=1) + "\n")
                clock = (f"offset {measured['offset']}s" if "offset" in measured
                         else f"{len(measured['sections'])} sections")
                why = f"the same feed as x-{twin}, {clock}"
            else:
                what, why = "wait", f"could not hear it against x-{twin}"
        if what == "wait":
            tries = row.get("tries", 0) + 1
            if tries >= MAX_TRIES:
                what, why = "failed", f"{why} ({tries} tries; left for a person)"
            row["tries"] = tries
        print(f"{post}  {what}: {why}")
        state["posts"][post] = {**row, "account": got.get("account", row.get("account", "")),
                                "what": what, "why": why,
                                "at": datetime.now(UTC).strftime("%Y-%m-%d")}
        return True

    with tempfile.TemporaryDirectory() as tmp:
        later = [post for post in sorted(dict.fromkeys(posts), key=int)
                 if not handle(post, Path(tmp), last_pass=False)]
        for post in later:
            handle(post, Path(tmp), last_pass=True)

    if not args.dry_run:
        if found:
            state["newest"] = max(found + ([state["newest"]] if state.get("newest") else []), key=int)
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    print(f"\n{indexed} indexed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

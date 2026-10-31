"""Place clips posted on X in the archive, for the extension's clip panel.

    .venv/bin/python scripts/place_x_clips.py POST_ID [POST_ID ...]
    .venv/bin/python scripts/place_x_clips.py --file ids.txt

Ansem posts several clips a day, a minute or so each, cut from Market
Bubble and his own videos. The clip ends where the clipper stopped; the
archive has the rest. For each post this:

  1. reads X's own caption track for the clip, through yt-dlp. No X API
     credit (those are the bot's) and no model call: X captions uploaded
     videos itself, word by word.
  2. places it with app.clipmatch, the same unique eight-word runs the bot
     was going to use, against every episode in data/episodes.json.
  3. keeps what was said just before the clip and just after it, and
     writes the lot to data/x_clips.json, which the service ships.

A clip that can't be placed is left out and the reason printed. Nothing
is ever guessed: a clip from a show the archive doesn't hold matches
nothing, and gets no button.

Placed on 31 Oct 2026: 9 of Ansem's last 10 clips, every one to the line.
The tenth was his Halloween video, which isn't in the archive.

Longer posts (full conversations, whole broadcasts) are not clips: X has
no captions for most of them, and the panel for a whole recording is the
summary one. They are reported and skipped.

One post at a time with a pause between, the same courtesy the YouTube
lookups get.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import clipmatch, episode_store, x_posts  # noqa: E402

OUT = ROOT / "data" / "x_clips.json"
POST_ID = re.compile(r"^[0-9]{5,25}$")

# Longer than this is a whole recording posted, not a clip.
MAX_CLIP_SECONDS = 600

# How much of the archive to keep either side. "Before" is a lead-in, so
# a sentence or two; "after" is the point of the panel, the part the
# clipper cut.
BEFORE_SECONDS, BEFORE_WORDS = 30, 70
AFTER_SECONDS, AFTER_WORDS = 90, 230

PAUSE_SECONDS = 2.0


def caption_text(vtt: str) -> str:
    """The words of a WebVTT caption track, in order, as one line.

    X wraps every cue in an <X-word-ms ...> tag carrying per-word timing;
    the words are what's needed, so tags, timings and cue numbers go.
    """
    out = []
    for line in vtt.splitlines():
        line = line.strip()
        if (not line or line == "WEBVTT" or "-->" in line or line.isdigit()
                or line.startswith(("NOTE", "STYLE", "Kind:", "Language:"))):
            continue
        out.append(re.sub(r"<[^>]+>", "", line).strip())
    return " ".join(w for w in out if w)


def choose(found: list[dict]) -> dict | None:
    """Which copy of the show to point at.

    Every candidate passed both of clipmatch's gates, so they are the same
    tape held twice (see clipmatch.candidates). The copy indexed from X
    wins: its times open the X broadcast at the second, where the show
    aired. YouTube only when X has no copy.
    """
    if not found:
        return None
    on_x = [c for c in found if c["episode_id"].startswith("x-")]
    return (on_x or found)[0]


def _stamped(episode: dict) -> tuple[list[tuple[str, float]], list[tuple[str, int]]]:
    """(display words with their segment's second, normalised tokens with
    the index of the display word each came from)."""
    shown: list[tuple[str, float]] = []
    tokens: list[tuple[str, int]] = []
    for segment in episode.get("segments") or ():
        at = float(segment.get("t", 0.0))
        for word in str(segment.get("text") or "").split():
            for token in clipmatch.words(word):
                tokens.append((token, len(shown)))
            shown.append((word, at))
    return shown, tokens


def context(episode: dict, caption: str, start: float, duration: float) -> dict | None:
    """Where the clip begins and ends in the episode's own words, and what
    was said either side of it.

    Found with the clip's own runs, in the stretch the matcher placed it:
    the first run of the clip the episode holds marks its first word, the
    last one its last. So "after" begins at the word after the clip's last,
    not at the next segment, which could repeat its closing words or skip
    the rest of a sentence it cut.
    """
    shown, tokens = _stamped(episode)
    lo, hi = start - 60, start + duration + 60
    where: dict[str, int] = {}
    for i in range(len(tokens) - clipmatch.GRAM + 1):
        if lo <= shown[tokens[i][1]][1] <= hi:
            key = " ".join(t for t, _ in tokens[i:i + clipmatch.GRAM])
            where.setdefault(key, i)
    hits = [where[r] for r in clipmatch.runs(caption) if r in where]
    if not hits:
        return None
    first = tokens[min(hits)][1]
    last = tokens[min(max(hits) + clipmatch.GRAM - 1, len(tokens) - 1)][1]
    begins, ends = shown[first][1], shown[last][1]

    before = [w for w, t in shown[:first] if t >= begins - BEFORE_SECONDS][-BEFORE_WORDS:]
    after_words = shown[last + 1:]
    after = [w for w, t in after_words if t <= ends + AFTER_SECONDS][:AFTER_WORDS]
    return {
        "start": int(begins),
        "end": int(ends),
        "before": " ".join(before),
        "after": " ".join(after),
        "after_at": int(after_words[0][1]) if after else None,
    }


def fetch(post: str, workdir: Path) -> dict:
    """The clip's caption, length and account, from X through yt-dlp."""
    url = f"https://x.com/i/status/{post}"
    # --playlist-items 1: a post that quotes another video comes back as a
    # list of two, its own clip first and the quoted post's video second.
    # Ansem's clips often quote the whole video they were cut from, and
    # reading the second one placed a 34-minute recording as the clip.
    # --no-simulate: --print alone downloads nothing, captions included.
    run = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "yt-dlp"), "--socket-timeout", "30",
         "--no-warnings", "--playlist-items", "1", "--no-simulate", "--skip-download",
         "--write-subs", "--sub-langs", "en", "--sub-format", "vtt",
         "-o", str(workdir / f"{post}.%(ext)s"),
         "--print", "%(duration)s\t%(uploader_id)s\t%(extractor)s", url],
        capture_output=True, text=True, timeout=180)
    if run.returncode != 0:
        return {"error": "X would not give the post: " + (run.stderr.strip().splitlines() or ["?"])[-1][:160]}
    duration, account, extractor = (run.stdout.strip().splitlines() or ["\t\t"])[-1].split("\t")
    vtt = workdir / f"{post}.en.vtt"
    return {
        "duration": float(duration) if re.match(r"^\d+(\.\d+)?$", duration) else None,
        "account": account if re.match(r"^[A-Za-z0-9_]{1,15}$", account) else "",
        "broadcast": extractor.endswith("broadcast"),
        "caption": caption_text(vtt.read_text()) if vtt.exists() else "",
    }


def place_one(post: str, got: dict, episodes: list[dict]) -> tuple[dict | None, str]:
    """(the entry for x_clips.json, or None; what happened, for the log)."""
    if got.get("error"):
        return None, got["error"]
    if got.get("account") not in x_posts.CLIP_ACCOUNTS:
        return None, f"posted by @{got.get('account') or '?'}, not one of the show's accounts"
    if got.get("broadcast"):
        return None, "a broadcast, not a clip"
    duration = got.get("duration")
    if not duration:
        return None, "no length given"
    if duration > MAX_CLIP_SECONDS:
        return None, f"{duration / 60:.0f} minutes long, a whole recording rather than a clip"
    if not got.get("caption"):
        return None, "X has no captions for it"
    found = clipmatch.candidates(got["caption"], episodes, duration)
    best = choose(found)
    if best is None:
        return None, "not in the archive"
    episode = next(e for e in episodes if e.get("episode_id") == best["episode_id"])
    where = context(episode, got["caption"], best["start"], duration)
    if where is None:
        return None, "placed, but its words could not be lined up"
    entry = {
        "account": got.get("account", ""),
        "archive": "podcast",
        "episode_id": best["episode_id"],
        **where,
        "duration": round(duration, 1),
        "matches": best["matches"],
        "copies": len(found),
        "placed": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    return entry, f"{best['episode_id']} at {_clock(where['start'])}, {best['matches']} runs"


def _clock(seconds: int) -> str:
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("posts", nargs="*", help="X post ids")
    ap.add_argument("--file", type=Path, help="a file of post ids, one a line")
    args = ap.parse_args()
    posts = list(args.posts)
    if args.file:
        posts += [line.strip() for line in args.file.read_text().splitlines() if line.strip()]
    bad = [p for p in posts if not POST_ID.match(p)]
    if bad:
        sys.exit(f"not post ids: {bad[:5]}")

    episodes = episode_store.load()
    clips = json.loads(OUT.read_text()) if OUT.exists() else {}
    placed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for n, post in enumerate(dict.fromkeys(posts)):
            if n:
                time.sleep(PAUSE_SECONDS)
            got = fetch(post, Path(tmp))
            entry, said = place_one(post, got, episodes)
            print(f"{post}  {said}")
            if entry:
                clips[post] = entry
                placed += 1
            elif not got.get("error"):
                # Placed before and not now means the archive changed under
                # it; an old answer must not outlive the reason for it. A
                # failed download says nothing either way, so it keeps it.
                clips.pop(post, None)
    OUT.write_text(json.dumps(dict(sorted(clips.items())), indent=1, ensure_ascii=False) + "\n")
    print(f"\n{placed} of {len(dict.fromkeys(posts))} placed; {len(clips)} clips in {OUT.name}")


if __name__ == "__main__":
    main()

"""Find the X broadcast of each MCG Live show and measure it against YouTube.

    .venv/bin/python scripts/pair_mcg_broadcasts.py
    .venv/bin/python scripts/pair_mcg_broadcasts.py --dry-run

MCG goes live on YouTube and on X at the same time. The archive is the
YouTube copy; this finds the X one, so a citation can open on X at the
same second (app/mcg_on_x.py says why that is worth having).

Finding it costs money, a little. yt-dlp cannot list an account's
broadcasts, so the question goes to X's search, which charges for each
post it returns. The query matches only posts from @MCGlive that link the
broadcast player, which is one a day, and it starts after the newest post
already paired, so a morning pays for the show that aired since: about
half a cent. A walk of the timeline for the same answer was 25 posts for
28 hours of it.

Pairing is by title, because MCG gives both copies the same one. That is
a hint and not the proof. The proof is the audio: ninety seconds of the
X broadcast, taken twenty minutes in and again half an hour from the end,
transcribed and matched against the transcript already indexed. Eight
word runs that occur once in each, as build_youtube_map does it. Both
points have to land on the same offset, or the broadcast is not paired:
a stream that dropped and came back is two recordings on X and one on
YouTube, and one number would be wrong for half of it.

Nothing is embedded, indexed or posted. It writes one small file.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import html
import json
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import get_settings, redact  # noqa: E402
from app.x_api import API, XCredentials  # noqa: E402
from scripts.find_new_broadcasts import broadcast_link  # noqa: E402

SHELF = ROOT / "data" / "mcg_index.json"
OUT = ROOT / "data" / "mcg_broadcast_links.json"
SHOW = "MCGlive"
QUERY = f'from:{SHOW} url:"i/broadcasts" -is:retweet -is:reply'

RUN = 8            # words per run, as in build_youtube_map
SLICE = 90         # seconds of the broadcast listened to at each point
WITHIN = 5.0       # a run agrees if it is this close to the median
ENOUGH = 20        # shared runs needed before a slice counts as evidence
AGREE = 0.9        # and this share of them must agree

_WORD = re.compile(r"[a-z0-9']+")
_LINK = re.compile(r"https?://\S+")


def title_words(text: str) -> list[str]:
    """A title as its words, so an emoji, an escaped ampersand or the
    t.co link X appends to the post do not make two titles differ."""
    return _WORD.findall(_LINK.sub(" ", html.unescape(text or "")).lower())


def candidates(post: dict, shelf: list[dict], paired: set[str]) -> list[dict]:
    """Shows this broadcast could be, likeliest first.

    The one with the same title, then any stream shelved for the day it
    aired or the day after: the date on the shelf is the upload's, and a
    show that starts at 16:00 UTC is the same day there. Order only saves
    time. Whether it IS the show is decided by listening.
    """
    want = title_words(post.get("text", ""))
    day = (post.get("created_at") or "")[:10]
    open_rows = [r for r in shelf if r.get("id") not in paired]
    same = [r for r in open_rows if want and title_words(r.get("title", "")) == want]
    near = [r for r in open_rows if r not in same
            and r.get("format") == "stream"
            and day and 0 <= _days(day, r.get("published_at", "")) <= 1]
    return same + near


def _days(first: str, second: str) -> int:
    from datetime import date
    try:
        return (date.fromisoformat(second[:10]) - date.fromisoformat(first[:10])).days
    except ValueError:
        return 99


def _runs(lines: list[dict], shift: float = 0.0) -> dict[str, list[float]]:
    """Every eight-word run and the second it starts, spread across the
    line it is in: a line is about eight seconds, and a run from the end
    of one is not at the second the line began."""
    starts = [float(s.get("start", s.get("t", 0))) for s in lines]
    stream: list[tuple[str, float]] = []
    for i, line in enumerate(lines):
        words = _WORD.findall((line.get("text") or "").lower())
        until = float(line.get("end", starts[i + 1] if i + 1 < len(starts)
                               else starts[i] + 8))
        span = max(until - starts[i], 0.1)
        stream += [(w, shift + starts[i] + span * n / max(len(words), 1))
                   for n, w in enumerate(words)]
    runs: dict[str, list[float]] = collections.defaultdict(list)
    for i in range(len(stream) - RUN + 1):
        runs[" ".join(w for w, _ in stream[i:i + RUN])].append(stream[i][1])
    return runs


def offset(heard: list[dict], at: float, indexed: list[dict]) -> float | None:
    """What to add to a YouTube second to reach the same words on X, at
    this point in the show. None when the slice is not evidence that the
    two are the same recording.

    `heard` is the slice's own transcript, on a clock starting at zero;
    `at` is where in the broadcast the slice was taken.
    """
    mine, theirs = _runs(heard, shift=at), _runs(indexed)
    gaps = [mine[run][0] - theirs[run][0] for run in mine
            if len(mine[run]) == 1 and len(theirs.get(run, ())) == 1]
    if len(gaps) < ENOUGH:
        return None
    middle = statistics.median(gaps)
    if sum(abs(g - middle) <= WITHIN for g in gaps) / len(gaps) < AGREE:
        return None
    return middle


def settle(first: float | None, second: float | None) -> int | None:
    """One offset for the whole show, only if both ends of it agree."""
    if first is None or second is None or abs(first - second) > WITHIN:
        return None
    return round((first + second) / 2)


def listening_points(seconds: float) -> list[int]:
    """Twenty minutes in, past any waiting screen, and half an hour from
    the end. A short show is heard at a third and two thirds instead."""
    if seconds >= 3600:
        return [1200, int(seconds) - 1800]
    return [int(seconds / 3), int(seconds * 2 / 3)]


def hear(broadcast: str, at: int, work: Path) -> list[dict]:
    """Ninety seconds of the broadcast from `at`, transcribed."""
    from scripts.ingest_mcg import groq_key, transcribe_via_groq

    target = work / f"x_{at}.mp3"
    proc = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "yt-dlp"), "--no-warnings", "-q",
         "-f", "bestaudio/worst", "--download-sections", f"*{at}-{at + SLICE}",
         "-x", "--audio-format", "mp3", "--audio-quality", "7",
         "-o", str(work / f"x_{at}.%(ext)s"), broadcast],
        capture_output=True, text=True, check=False)
    if not target.exists():
        raise RuntimeError((proc.stderr.strip().splitlines() or ["no audio"])[-1][:200])
    return transcribe_via_groq(target, groq_key())


def measure(broadcast: str, row: dict) -> int | None:
    from scripts.make_clip import mcg_segments

    indexed = mcg_segments(row["id"])
    if not indexed:
        return None
    found = []
    with tempfile.TemporaryDirectory() as tmp:
        for at in listening_points(row.get("seconds") or 0):
            found.append(offset(hear(broadcast, at, Path(tmp)), at, indexed))
    return settle(*found)


async def broadcast_posts(since_id: str | None) -> list[dict]:
    settings = get_settings()
    cred = XCredentials(settings.x_api_key, settings.x_api_secret,
                        settings.x_access_token, settings.x_access_secret)
    url = f"{API}/tweets/search/recent"
    params = {"query": QUERY, "max_results": "20",
              "tweet.fields": "created_at,entities"}
    if since_id:
        params["since_id"] = since_id
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.get(
            url, params=params,
            headers={"Authorization": cred.header("GET", url, params)})
    if response.status_code != 200:
        raise RuntimeError(f"X search answered HTTP {response.status_code}")
    return [p for p in response.json().get("data") or [] if broadcast_link(p)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="find and measure, write nothing")
    args = ap.parse_args()

    pairs = json.loads(OUT.read_text()) if OUT.exists() else {}
    shelf = json.loads(SHELF.read_text())
    # Only posts newer than the newest one paired. A broadcast found before
    # its YouTube copy was indexed is not paired, so it is not the newest
    # paired, so tomorrow's search returns it again.
    newest = max((p["post"] for p in pairs.values()), key=int, default=None)
    try:
        posts = asyncio.run(broadcast_posts(newest))
    except Exception as exc:  # noqa: BLE001
        print(f"  could not ask X: {redact(str(exc))[:200]}")
        return 1
    if not posts:
        print("  no new MCG broadcast on X")
        return 0

    added = 0
    for post in sorted(posts, key=lambda p: int(p["id"])):
        broadcast = broadcast_link(post)
        label = f"{post.get('created_at', '')[:10]} {broadcast.rsplit('/', 1)[-1]}"
        if any(p["broadcast"] == broadcast for p in pairs.values()):
            continue
        rows = candidates(post, shelf, set(pairs))
        if not rows:
            print(f"  {label}: its YouTube copy is not indexed yet")
            continue
        for row in rows:
            try:
                gap = measure(broadcast, row)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label}: could not listen ({redact(str(exc))[:160]})")
                break
            if gap is None:
                continue
            pairs[row["id"]] = {"broadcast": broadcast, "offset": gap,
                                "post": post["id"]}
            added += 1
            print(f"  {label}: {row['id']}, X {gap:+d}s against YouTube  "
                  f"{row['title'][:50]}")
            break
        else:
            print(f"  {label}: no indexed show matches what it says; left alone")

    if added and not args.dry_run:
        OUT.write_text(json.dumps(pairs, indent=1, sort_keys=True) + "\n")
        print(f"  wrote {OUT.relative_to(ROOT)}: {len(pairs)} shows on X")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Find the X broadcast of each MCG Live show and measure it against YouTube.

    .venv/bin/python scripts/pair_mcg_broadcasts.py
    .venv/bin/python scripts/pair_mcg_broadcasts.py --dry-run
    .venv/bin/python scripts/pair_mcg_broadcasts.py --pair <video id> <broadcast link> [--post <id>]

MCG goes live on YouTube and on X at the same time. The archive is the
YouTube copy; this finds the X one, so a citation can open on X at the
same second (app/mcg_on_x.py says why that is worth having).

Finding it costs money, a little. yt-dlp cannot list an account's
broadcasts, so the question goes to X's search, which charges for each
post it returns. The query matches only posts from @MCGlive that link the
broadcast player, which is one a day, and it starts after the newest post
already paired, so a morning pays for the show that aired since: about
half a cent. A walk of the timeline for the same answer was 25 posts for
28 hours of it. X's recent search reaches back seven days and refuses a
since_id older than that, so a newest post that old is not passed: the
search is then the whole week, a few posts more.

Pairing is by title, because MCG gives both copies the same one. That is
a hint and not the proof. The proof is the audio: ninety seconds of the
X broadcast, transcribed and matched against the transcript already
indexed, eight word runs that occur once in each, as build_youtube_map
does it. It is heard every twenty minutes from twenty minutes in to half
an hour from the end.

The two clocks do not always stay the same distance apart. 21, 23 and
26 October each jump partway through (26 October is 1.5 seconds apart
for two hours and twenty minutes, then 20), so where two neighbouring
slices disagree, the stretch between them is heard again at its middle
until the change is pinned to within three minutes. The show is stored
as stretches of YouTube time with the offset that holds across each, and
the few minutes around a change are left out of every stretch: a link
into them stays on YouTube rather than open on the wrong words. A show
with one offset throughout is stored as one number, as before.

--pair measures a broadcast that the search can no longer return (it is
older than the week) or that was found by hand. It costs no X reads.

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
import time
from collections.abc import Callable
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
STEP = 1200        # seconds between slices across a show
PRECISION = 180    # a change of offset is narrowed to within this
SEARCH_DAYS = 6.5  # X's recent search covers seven; keep clear of the edge

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


def listening_points(seconds: float) -> list[int]:
    """Twenty minutes in, past any waiting screen, then every twenty
    minutes to half an hour from the end. A short show is heard at a
    third and two thirds instead."""
    if seconds < 3600:
        return [int(seconds / 3), int(seconds * 2 / 3)]
    last = int(seconds) - 1800
    return list(range(1200, last, STEP)) + [last]


def _same(first: float | None, second: float | None) -> bool:
    """Two slices tell the same story: one offset, or neither is the show."""
    if first is None or second is None:
        return first is None and second is None
    return abs(first - second) <= WITHIN


def chart(listen: Callable[[int], float | None],
          points: list[int]) -> list[tuple[int, float | None]]:
    """Every point heard, and where two neighbours disagree, the middle of
    the stretch between them, again and again until the change is pinned
    to within PRECISION seconds. A middle that agrees with neither side is
    a third story; that stretch is left as it is."""
    heard = [(at, listen(at)) for at in points]
    found = list(heard)
    for (left, first), (right, second) in zip(heard, heard[1:], strict=False):
        while not _same(first, second) and right - left > PRECISION:
            middle = (left + right) // 2
            gap = listen(middle)
            found.append((middle, gap))
            if _same(gap, first):
                left = middle
            elif _same(gap, second):
                right = middle
            else:
                break
    return sorted(found, key=lambda point: point[0])


def stretches(found: list[tuple[int, float | None]]) -> list[dict] | None:
    """The show as stretches of YouTube time, each with the offset that
    holds across it, or None when fewer than two slices were the show.

    A stretch runs from its first slice to the end of its last. The first
    one opens at the start of the show and the last runs to its end, as
    one offset always did, unless a slice that was not the show stands
    before or after it. Between two stretches is where the clocks moved,
    somewhere in the minutes `chart` narrowed it to, so no link lands there.
    """
    if sum(gap is not None for _, gap in found) < 2:
        return None
    runs: list[list[tuple[int, float]]] = []
    for i, (at, gap) in enumerate(found):
        if gap is None:
            continue
        before = found[i - 1][1] if i else None
        if runs and before is not None and _same(before, gap):
            runs[-1].append((at, gap))
        else:
            runs.append([(at, gap)])
    out = []
    for run in runs:
        shift = round(statistics.median(gap for _, gap in run))
        opens = run[0] == found[0]
        closes = run[-1] == found[-1]
        out.append({"from": 0 if opens else max(0, run[0][0] - shift),
                    "to": None if closes else run[-1][0] + SLICE - shift,
                    "offset": shift})
    return out


def shape(found: list[dict]) -> dict:
    """What goes in the file: one number for a show that kept one offset
    throughout, the stretches for one that did not."""
    if len(found) == 1 and found[0]["from"] == 0 and found[0]["to"] is None:
        return {"offset": found[0]["offset"]}
    return {"sections": found}


def described(pair: dict) -> str:
    if "sections" not in pair:
        return f"X {pair['offset']:+d}s against YouTube"
    return "X " + ", ".join(
        f"{s['offset']:+d}s from {s['from'] // 60}m" for s in pair["sections"])


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


def measure(broadcast: str, row: dict) -> dict | None:
    from scripts.make_clip import mcg_segments

    indexed = mcg_segments(row["id"])
    if not indexed:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        found = chart(lambda at: offset(hear(broadcast, at, Path(tmp)), at, indexed),
                      listening_points(row.get("seconds") or 0))
    mapped = stretches(found)
    return shape(mapped) if mapped else None


def since(pairs: dict[str, dict], now: float | None = None) -> str | None:
    """The newest paired post to search after, while X still accepts it.
    A pair made by hand may have no post."""
    newest = max((p["post"] for p in pairs.values() if p.get("post")),
                 key=int, default=None)
    if newest is None:
        return None
    posted = ((int(newest) >> 22) + 1288834974657) / 1000   # X's snowflake clock
    now = time.time() if now is None else now
    return newest if now - posted < SEARCH_DAYS * 86400 else None


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
    ap.add_argument("--pair", nargs=2, metavar=("VIDEO", "BROADCAST"),
                    help="measure this show against this broadcast; no X reads")
    ap.add_argument("--post", help="with --pair: the post that linked the broadcast")
    args = ap.parse_args()

    pairs = json.loads(OUT.read_text()) if OUT.exists() else {}
    shelf = json.loads(SHELF.read_text())
    if args.pair:
        return pair_by_hand(pairs, shelf, *args.pair, args.post, args.dry_run)
    # Only posts newer than the newest one paired. A broadcast found before
    # its YouTube copy was indexed is not paired, so it is not the newest
    # paired, so tomorrow's search returns it again.
    try:
        posts = asyncio.run(broadcast_posts(since(pairs)))
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
                clocks = measure(broadcast, row)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label}: could not listen ({redact(str(exc))[:160]})")
                break
            if clocks is None:
                continue
            pairs[row["id"]] = {"broadcast": broadcast, "post": post["id"], **clocks}
            added += 1
            print(f"  {label}: {row['id']}, {described(clocks)}  {row['title'][:50]}")
            break
        else:
            print(f"  {label}: no indexed show matches what it says; left alone")

    if added and not args.dry_run:
        write(pairs)
    return 0


def pair_by_hand(pairs: dict[str, dict], shelf: list[dict], video: str,
                 broadcast: str, post: str | None, dry_run: bool) -> int:
    row = next((r for r in shelf if r.get("id") == video), None)
    if row is None:
        print(f"  {video} is not on the MCG shelf")
        return 1
    broadcast = broadcast.split("?", 1)[0]
    clocks = measure(broadcast, row)
    if clocks is None:
        print(f"  {video}: {broadcast} is not this show; left alone")
        return 1
    pairs[video] = {"broadcast": broadcast, **({"post": post} if post else {}), **clocks}
    print(f"  {video}: {described(clocks)}  {row['title'][:50]}")
    if not dry_run:
        write(pairs)
    return 0


def write(pairs: dict[str, dict]) -> None:
    OUT.write_text(json.dumps(pairs, indent=1, sort_keys=True) + "\n")
    print(f"  wrote {OUT.relative_to(ROOT)}: {len(pairs)} shows on X")


if __name__ == "__main__":
    raise SystemExit(main())

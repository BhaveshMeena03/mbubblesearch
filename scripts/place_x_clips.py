"""Place clips posted on X in the archive, for the extension's clip panel.

    .venv/bin/python scripts/place_x_clips.py --search
    .venv/bin/python scripts/place_x_clips.py POST_ID [POST_ID ...]
    .venv/bin/python scripts/place_x_clips.py --file ids.txt

--search is what the morning job runs: one X search for the video posts
the clip accounts (x_posts.CLIP_ACCOUNTS: Ansem, Market Bubble, ours,
Kaiz and the clippers) made since the newest one already read, then each
is placed. X charges for each post the search returns, about half a
cent, and the newest id is kept in data/x_clips_search.json so none is
paid for twice.
The search reaches back seven days, which is also how far a first run
goes.

Ansem posts several clips a day, a minute or so each, cut from Market
Bubble and his own videos. The clip ends where the clipper stopped; the
archive has the rest. For each post this:

  1. reads X's own caption track for the clip, through yt-dlp. No X API
     credit (those are the bot's) and no model call: X captions uploaded
     videos itself, word by word. Not every one, though: on 2 Nov 2026,
     139 of 192 video posts from the clip accounts had no track, ours
     among them. Those are heard instead: the clip's audio, a minute or
     two, through Groq's Whisper, the archive's own transcriber.
  2. places it with app.clipmatch, the same unique eight-word runs the bot
     was going to use, against every episode in data/episodes.json, and
     against the MCG Live and ThreadGuy shows of the week before the post.
     Those two archives keep their lines only in Pinecone, so each show is
     read back once (app.mcg_transcript.rebuild, one metadata query) and
     kept on this Mac (app.local_cache), never read twice.
  3. keeps what was said just before the clip and just after it, and
     writes the lot to data/x_clips.json, which the service ships.

A clip that can't be placed is left out and the reason printed. Nothing
is ever guessed: a clip from a show the archive doesn't hold matches
nothing, and gets no button.

Placed on 31 Oct 2026: 9 of Ansem's last 10 clips, every one to the line.
The tenth was his Halloween video, which isn't in the archive.

A broadcast is not a clip: the panel for a whole recording is the summary
one. A long upload is placed like any clip when it is a cut of a show the
archive holds: MCG posts every interview from its daily show on its own,
11 to 65 minutes, and on 3 Nov 2026 Ansem posted 34 minutes of Episode 24
as "Market Bubble ep.25". One the archive doesn't hold matches nothing.

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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    clipmatch,
    episode_store,
    local_cache,
    mcg_transcript,
    x_posts,
)

OUT = ROOT / "data" / "x_clips.json"
SEARCHED = ROOT / "data" / "x_clips_search.json"
POST_ID = re.compile(r"^[0-9]{5,25}$")

# Longer than this is not a cut of any show the archive holds; the longest
# Market Bubble episode runs under four hours.
MAX_CLIP_SECONDS = 4 * 3600

# Longer than this, an upload may be stitched from several parts of a show
# (clipmatch.held_in). A clip this short is one stretch or nothing.
STITCHED_FROM_SECONDS = 600

# How much of the archive to keep either side. "Before" is a lead-in, so
# a sentence or two; "after" is the point of the panel, the part the
# clipper cut.
BEFORE_SECONDS, BEFORE_WORDS = 30, 70
AFTER_SECONDS, AFTER_WORDS = 90, 230

PAUSE_SECONDS = 2.0

# MCG Live and ThreadGuy: the shows a clip is matched against, and the X
# pairs that say which of them aired on X. A clip goes up within days of
# its show; a week back, and a day ahead for time zones.
SHOWS = {"mcg": ROOT / "data" / "mcg_index.json",
         "threadguy": ROOT / "data" / "threadguy_index.json"}
PAIRS = {"mcg": ROOT / "data" / "mcg_broadcast_links.json",
         "threadguy": ROOT / "data" / "threadguy_broadcast_links.json"}
LOOKBACK_DAYS = 7
X_EPOCH_MS = 1288834974657
# A clip posted before what it is from was indexed: two of Ansem's 30 Oct
# clips were cut from his Hyperliquid x ICE video, indexed on 3 Nov, and
# were refused on the 1st and never looked at again. Ansem clips Thursday's
# show the night it airs, before the Friday watcher may have it, so this
# would keep happening. The clips of the two accounts whose own recordings
# the archive indexes are tried again for a week, on any run after the
# archive has grown, and on no other.
RETRY_ACCOUNTS = frozenset({"blknoiz06", "marketbubble"})
RETRY_DAYS = 7


def _today() -> date:
    return datetime.now(UTC).date()


def posted_on(post: str) -> date:
    """The day a post went up, from its id: X ids carry their own time."""
    return datetime.fromtimestamp(((int(post) >> 22) + X_EPOCH_MS) / 1000, UTC).date()


def _json_rows(path: Path) -> list[dict]:
    rows = json.loads(path.read_text()) if path.exists() else []
    rows = rows if isinstance(rows, list) else rows.get("episodes", [])
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


class Shows:
    """The MCG Live and ThreadGuy shows a clip could be from, with their
    lines, read from the index only the first time one is asked for."""

    def __init__(self) -> None:
        self.rows = {archive: _json_rows(path) for archive, path in SHOWS.items()}
        self.paired = {video for path in PAIRS.values() if path.exists()
                       for video, pair in json.loads(path.read_text()).items()
                       if isinstance(pair, dict) and pair.get("broadcast")
                       and (pair.get("sections") or "offset" in pair)}
        self.unread: set[str] = set()
        self.missed = False             # a show the last before() needed was unreadable
        self._lines: dict[tuple[str, str], list[dict]] = {}
        self._index: dict[str, object] = {}

    def before(self, day: date) -> list[dict]:
        """Every show of the week up to `day`, as clipmatch wants them."""
        lo = (day - timedelta(days=LOOKBACK_DAYS)).isoformat()
        hi = (day + timedelta(days=1)).isoformat()
        out = []
        for archive, rows in self.rows.items():
            for row in rows:
                if not lo <= str(row.get("published_at") or "")[:10] <= hi:
                    continue
                lines = self.lines(archive, str(row["id"]))
                self.missed |= str(row["id"]) in self.unread
                if lines:
                    out.append({"episode_id": str(row["id"]), "title": row.get("title", ""),
                                "archive": archive, "segments": lines})
        return out

    def lines(self, archive: str, video: str) -> list[dict]:
        key = (archive, video)
        if key not in self._lines:
            got = local_cache.load_lines(archive, video)
            if got is None:
                try:
                    got = self._read(archive, video)
                except Exception as exc:                        # noqa: BLE001
                    from app.config import redact
                    self.unread.add(video)
                    print(f"     {archive} {video} could not be read: "
                          f"{type(exc).__name__}: {redact(str(exc))[:120]}")
                    got = []
                else:
                    if got:
                        local_cache.put_lines(archive, video, got)
            self._lines[key] = got
        return self._lines[key]

    def _read(self, archive: str, video: str) -> list[dict]:
        from app.config import get_settings
        from app.podcast import PodcastIndex

        settings = get_settings()
        namespace = settings.mcg_namespace if archive == "mcg" else settings.threadguy_namespace
        if archive not in self._index:
            # MCG has an index of its own; ThreadGuy is a namespace in the
            # default one, the same split summarize_mcg.py and
            # summarize_threadguy.py open them with.
            self._index[archive] = (
                PodcastIndex(namespace=namespace, index_name=settings.mcg_pinecone_index).index
                if archive == "mcg" else PodcastIndex(namespace=namespace).index)
        return mcg_transcript.rebuild(self._index[archive], namespace,
                                      settings.embedding_dimension, video)


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


def choose(found: list[dict], paired: set[str] = frozenset()) -> dict | None:
    """Which copy of the show to point at.

    Every candidate passed both of clipmatch's gates, so they are the same
    tape held twice (see clipmatch.candidates). The copy indexed from X
    wins: its times open the X broadcast at the second, where the show
    aired. Then a YouTube show paired with its X broadcast, over a cut of
    it uploaded on its own: MCG posts each interview that way, so a clip
    of one matches the daily show and the cut alike. YouTube only when X
    has no copy.
    """
    if not found:
        return None
    on_x = ([c for c in found if c["episode_id"].startswith("x-")]
            or [c for c in found if c["episode_id"] in paired])
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
    try:
        run = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "yt-dlp"), "--socket-timeout", "30",
             "--no-warnings", "--playlist-items", "1", "--no-simulate", "--skip-download",
             "--write-subs", "--sub-langs", "en", "--sub-format", "vtt",
             "-o", str(workdir / f"{post}.%(ext)s"),
             "--print", "%(duration)s\t%(uploader_id)s\t%(extractor)s", url],
            capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"error": "X would not give the post: no answer in three minutes"}
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


def needs_ears(got: dict) -> bool:
    """A clip worth transcribing: one of ours to place, short, and with no
    caption track of its own."""
    duration = got.get("duration") or 0
    return (not got.get("error") and not got.get("broadcast") and not got.get("caption")
            and x_posts.clip_account(got.get("account"))
            and 0 < duration <= MAX_CLIP_SECONDS)


def listen(post: str, workdir: Path, seconds: float | None = None) -> str:
    """The clip's words, from its audio, or "" when it can't be had.

    The download is given five minutes plus the video's own length: a
    40-minute MCG upload took longer than five minutes on 3 Nov 2026, and
    the timeout, not caught then, ended the run before it wrote anything."""
    from scripts.ingest_mcg import groq_key, transcribe_via_groq

    try:
        run = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "yt-dlp"), "--socket-timeout", "30", "--no-warnings",
             "-q", "--playlist-items", "1", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
             "--audio-quality", "7", "-o", str(workdir / f"{post}-audio.%(ext)s"),
             f"https://x.com/i/status/{post}"],
            capture_output=True, text=True, timeout=300 + (seconds or 0))
    except subprocess.TimeoutExpired:
        return ""
    audio = workdir / f"{post}-audio.mp3"
    if run.returncode != 0 or not audio.exists():
        return ""
    try:
        return " ".join(s.get("text", "") for s in transcribe_via_groq(audio, groq_key())).strip()
    except Exception:                                           # noqa: BLE001
        return ""


def where_from(caption: str, pool: list[dict], duration: float) -> tuple[list[dict], bool]:
    """(every copy of a show this post is from, strongest first; whether it
    is stitched from several parts of it).

    A long upload has to be mostly the show. Its clip-length window is as
    wide as the upload, so a dozen runs inside it prove nothing: on 3 Nov
    2026 Ansem's own 37-minute video matched ThreadGuy's 29 Oct market open
    on 25 runs, the Hyperliquid announcement both of them played on air."""
    found = clipmatch.candidates(caption, pool, duration)
    if duration <= STITCHED_FROM_SECONDS:
        return found, False
    found = [row for row in found if row["share"] >= clipmatch.MIN_HELD_SHARE]
    return (found, False) if found else (clipmatch.held_in(caption, pool), True)


def place_one(post: str, got: dict, episodes, paired: set[str] = frozenset()
              ) -> tuple[dict | None, str]:
    """(the entry for x_clips.json, or None; what happened, for the log).

    `episodes` is the list, or a function giving it, called only once the
    post is known to be a clip worth placing, so a broadcast or a stranger's
    post never costs a read of the index."""
    if got.get("error"):
        return None, got["error"]
    if not x_posts.clip_account(got.get("account")):
        return None, f"posted by @{got.get('account') or '?'}, not one of the show's accounts"
    if got.get("broadcast"):
        return None, "a broadcast, not a clip"
    duration = got.get("duration")
    if not duration:
        return None, "no length given"
    if duration > MAX_CLIP_SECONDS:
        return None, f"{duration / 60:.0f} minutes long, a whole recording rather than a clip"
    if not got.get("caption"):
        return None, "no captions on X, and its audio could not be heard"
    pool = episodes() if callable(episodes) else episodes
    if any(e.get("episode_id") == f"x-{post}" for e in pool):
        return None, "the whole recording, indexed as itself"
    found, stitched = where_from(got["caption"], pool, duration)
    best = choose(found, paired)
    if best is None:
        return None, "not in the archive"
    episode = next(e for e in pool if e.get("episode_id") == best["episode_id"])
    # A stitched upload's words run from its first piece to its last.
    where = context(episode, got["caption"], best["start"],
                    best["end"] - best["start"] if stitched else duration)
    if where is None:
        return None, "placed, but its words could not be lined up"
    entry = {
        "account": got.get("account", ""),
        "archive": episode.get("archive", "podcast"),
        "episode_id": best["episode_id"],
        **where,
        "duration": round(duration, 1),
        "matches": best["matches"],
        "copies": len(found),
        "heard_from": "audio" if got.get("heard") else "captions",
        # Its own clock is not the show's: no time inside it plays in it.
        **({"stitched": True} if stitched else {}),
        "placed": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    return entry, (f"{best['episode_id']} at {_clock(where['start'])}, {best['matches']} runs"
                   + (", stitched from several parts" if stitched else ""))


def search_query(accounts=x_posts.CLIP_ACCOUNTS) -> str:
    """Video posts of their own from the clip accounts: no retweets (the
    video is someone else's post), no replies."""
    who = " OR ".join(f"from:{a}" for a in sorted(accounts))
    return f"({who}) has:videos -is:retweet -is:reply"


async def search(since_id: str | None, query: str | None = None) -> list[str]:
    """Post ids from X's recent search, oldest first: the clip accounts'
    videos, or what `query` asks for (add_x_recordings.py)."""
    import httpx

    from app.config import get_settings
    from app.x_api import API, XCredentials

    settings = get_settings()
    cred = XCredentials(settings.x_api_key, settings.x_api_secret,
                        settings.x_access_token, settings.x_access_secret)
    url = f"{API}/tweets/search/recent"
    found: list[str] = []
    token = None
    async with httpx.AsyncClient(timeout=30) as http:
        for _ in range(5):                       # 500 posts at most a run
            params = {"query": query or search_query(), "max_results": "100"}
            if since_id:
                params["since_id"] = since_id
            if token:
                params["next_token"] = token
            response = await http.get(url, params=params,
                                      headers={"Authorization": cred.header("GET", url, params)})
            if response.status_code != 200:
                raise RuntimeError(f"X search answered HTTP {response.status_code}")
            body = response.json()
            found += [p["id"] for p in body.get("data") or [] if POST_ID.match(p.get("id", ""))]
            token = (body.get("meta") or {}).get("next_token")
            if not token:
                break
    return sorted(set(found), key=int)


def _clock(seconds: int) -> str:
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("posts", nargs="*", help="X post ids")
    ap.add_argument("--file", type=Path, help="a file of post ids, one a line")
    ap.add_argument("--search", action="store_true",
                    help="find the clip accounts' new video posts on X first")
    args = ap.parse_args()
    posts = list(args.posts)
    newest = None
    state: dict = {}
    if args.search:
        import asyncio

        from app.config import redact
        state = json.loads(SEARCHED.read_text()) if SEARCHED.exists() else {}
        try:
            found = asyncio.run(search(state.get("newest")))
        except Exception as exc:                                # noqa: BLE001
            sys.exit(f"could not ask X: {type(exc).__name__}: {redact(str(exc))[:200]}")
        print(f"{len(found)} new video posts from {', '.join(sorted(x_posts.CLIP_ACCOUNTS))}")
        posts += found
        newest = max(found + ([state["newest"]] if state.get("newest") else []), key=int, default=None)
    if args.file:
        posts += [line.strip() for line in args.file.read_text().splitlines() if line.strip()]
    bad = [p for p in posts if not POST_ID.match(p)]
    if bad:
        sys.exit(f"not post ids: {bad[:5]}")

    episodes = [dict(e, archive="podcast") for e in episode_store.load()]
    held = len(episodes)
    today = _today()
    waiting = {post: row for post, row in (state.get("unplaced") or {}).items()
               if POST_ID.match(post) and isinstance(row, dict)
               and (today - posted_on(post)).days <= RETRY_DAYS}
    again = [post for post, row in waiting.items()
             if row.get("held", 0) < held and post not in posts]
    if again:
        print(f"{len(again)} refused before the archive grew, tried again")
    posts = again + posts
    shows = Shows()
    clips = json.loads(OUT.read_text()) if OUT.exists() else {}
    placed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for n, post in enumerate(dict.fromkeys(posts)):
            if n:
                time.sleep(PAUSE_SECONDS)
            got = fetch(post, Path(tmp))
            if needs_ears(got):
                got["caption"], got["heard"] = listen(post, Path(tmp), got.get("duration")), True
            shows.missed = False
            entry, said = place_one(
                post, got, lambda post=post: episodes + shows.before(posted_on(post)), shows.paired)
            print(f"{post}  {said}")
            # A failed download leaves it waiting as it was.
            if entry:
                waiting.pop(post, None)
            elif said == "not in the archive" and \
                    str(got.get("account") or "").lower() in RETRY_ACCOUNTS:
                waiting[post] = {"held": held}
            if entry:
                clips[post] = entry
                placed += 1
            elif not got.get("error") and not shows.missed:
                # Placed before and not now means the archive changed under
                # it; an old answer must not outlive the reason for it. A
                # failed download, or a show that could not be read, says
                # nothing either way, so it keeps it.
                clips.pop(post, None)
    OUT.write_text(json.dumps(dict(sorted(clips.items())), indent=1, ensure_ascii=False) + "\n")
    if args.search and (newest or waiting):
        # After the placing, so a run that dies partway reads them again.
        SEARCHED.write_text(json.dumps(
            {"newest": newest or state.get("newest"),
             **({"unplaced": dict(sorted(waiting.items()))} if waiting else {})}) + "\n")
    print(f"\n{placed} of {len(dict.fromkeys(posts))} placed; {len(clips)} clips in {OUT.name}")


if __name__ == "__main__":
    main()

"""Transcribe the verified Elon Musk sources into their own archive.

    .venv/bin/python scripts/ingest_elon.py            # everything pending
    .venv/bin/python scripts/ingest_elon.py --only dEv99vxKjVI
    .venv/bin/python scripts/ingest_elon.py --list

Writes data/elon_episodes.json. Deliberately not episodes.json: the two
archives never mix. @mbubbleSearch's whole standing is that it answers
from the Market Bubble broadcast, and an answer about that show sourced
from a Tesla interview would end that in one reply.

Only interviews. The Tesla earnings calls have the best provenance in the
whole list -- official channel, quarterly, dated by the title -- and they
are left out, because attribution fails on them. Voice clustering put 66%
of a call into one cluster that would not split at any number of clusters,
and the transcript's own hand-offs ("Thanks Lars", "our next question is
coming from Walt") disagreed with the clusters about who was speaking. On
an archive whose entire claim is "Elon said this", a quarter's guidance
from the CFO attributed to him is the failure that ends the project.

The same test on a two-person interview split 48/44 with the clusters
matching the speakers by content -- Lex's third-person introduction in
one, Elon answering in the first person in the other. That is the shape
the pipeline already handles on Market Bubble, so that is what is ingested.

Resumable. A 14-hour batch is hours of laptop time and something will
interrupt it; anything already transcribed is skipped.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.episode_store import load as load_episodes  # noqa: E402
from app.episode_store import merge
from app.provenance import admissible, drop_hallucinated  # noqa: E402

OUT = ROOT / "data" / "elon_episodes.json"
AUDIO_DIR = Path("/tmp/elon_audio")
# The project's own yt-dlp when there is one, otherwise whatever is on
# PATH. Hardcoding the venv path meant this script could only run from a
# checkout that had one: in a worktree it failed with "No such file or
# directory: .venv/bin/yt-dlp", which reads like a download failure and
# is not one. Episode #49 looked like a YouTube problem for a day because
# of it.
YTDLP = next((str(p) for p in (ROOT / ".venv" / "bin" / "yt-dlp",
                               Path.home() / "bullpen-concierge" / ".venv"
                               / "bin" / "yt-dlp")
              if p.is_file()), shutil.which("yt-dlp") or "yt-dlp")
COOKIES = Path.home() / "Downloads" / "cookies.txt"

# Verified by hand against the collector's output: every one is on the
# channel that recorded it. Adding to this list means checking that first.
SOURCES = [
    ("dEv99vxKjVI", "Lex Fridman", "2019-04-12",
     "Elon Musk: Tesla Autopilot | Lex Fridman Podcast #18"),
    ("smK9dgdTl40", "Lex Fridman", "2019-11-12",
     "Elon Musk: Neuralink, AI, Autopilot, and the Pale Blue Dot | "
     "Lex Fridman Podcast #49"),
    ("DxREm3s1scA", "Lex Fridman", "2021-12-28",
     "Elon Musk: SpaceX, Mars, Tesla Autopilot, Self-Driving, Robotics, "
     "and AI | Lex Fridman Podcast #252"),
    ("JN3KPFbWCy8", "Lex Fridman", "2023-11-09",
     "Elon Musk: War, AI, Aliens, Politics, Physics, Video Games, and "
     "Humanity | Lex Fridman Podcast #400"),
    ("Kbk9BiPhm7o", "Lex Fridman", "2024-08-02",
     "Elon Musk: Neuralink and the Future of Humanity | "
     "Lex Fridman Podcast #438"),

    # Rogan. Same two-person shape the pipeline handles, on the channel
    # that recorded it.
    #
    # The dates are not all YouTube's. #1609 and #2054 both report
    # 2024-06-27, which is the day JRE re-uploaded its Spotify-era back
    # catalogue, not the day they aired -- and a wrong date here is not
    # cosmetic: rule 4 of the Musk prompt uses dates to say when a view
    # was held, so a 2021 conversation stamped 2024 would report his
    # thinking as three years newer than it is. The pre-Spotify episodes
    # (#1169 in 2018, #1470 in May 2020) kept their real upload dates and
    # are taken from YouTube; the two re-uploads carry their original air
    # dates instead. Worth a check against the transcripts, since those
    # two are the only dates here that a person supplied.
    ("ycPr5-27vSI", "PowerfulJRE", "2018-09-07",
     "Joe Rogan Experience #1169 - Elon Musk"),
    ("RcYjXbSJBN8", "PowerfulJRE", "2020-05-07",
     "Joe Rogan Experience #1470 - Elon Musk"),
    ("Gbb2rV7Vpnw", "PowerfulJRE", "2021-02-11",
     "Joe Rogan Experience #1609 - Elon Musk"),
    ("tAJUwiAqW38", "PowerfulJRE", "2023-11-01",
     "Joe Rogan Experience #2054 - Elon Musk"),
    ("sSOxPJD-VNo", "PowerfulJRE", "2025-02-28",
     "Joe Rogan Experience #2281 - Elon Musk"),
    ("O4wBUysNe2k", "PowerfulJRE", "2025-10-31",
     "Joe Rogan Experience #2404 - Elon Musk"),

    # Interviews on the channel of the publication that recorded them,
    # which is the same provenance test the podcasts pass. Both are the
    # two-person shape the pipeline handles.
    #
    # The 2024 conversation with Trump is the Space itself, still up on
    # the account that hosted it. Every YouTube copy is a re-upload on
    # somebody else's channel -- the top result is labelled a supercut --
    # and an edit of a conversation cited to the second is the one thing
    # this archive cannot afford. X's own recording has no such problem.
    ("2BfMuHDfGJI", "New York Times Events", "2023-11-30",
     "Elon Musk on Advertisers, Trust and the “Wild Storm” in His "
     "Mind | DealBook Summit 2023"),
    ("XuoqKYxDHVc", "The Economist", "2026-07-29",
     "The full-length interview with Elon Musk | The Economist"),
    ("1nAKEpNkLwoxL", "Donald J. Trump", "2024-08-12",
     "Donald Trump and Elon Musk on X Spaces"),
]


def load() -> list[dict]:
    return load_episodes(OUT)


def source_url(vid: str) -> str:
    """Where this recording lives.

    An X Space is its own kind of source: the recording is hosted by X on
    the account that opened it, there is no second copy to confuse it
    with, and yt-dlp fetches it from the same URL a listener would open.
    Ids that are not YouTube's eleven characters are Space ids.
    """
    if len(vid) != 11:
        return f"https://x.com/i/spaces/{vid}"
    return f"https://www.youtube.com/watch?v={vid}"


def fetch_audio(video_id: str) -> Path:
    AUDIO_DIR.mkdir(exist_ok=True)
    path = AUDIO_DIR / f"{video_id}.m4a"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    if len(video_id) != 11:                       # an X Space, not YouTube
        done = subprocess.run(
            [YTDLP, "--no-warnings", "-f", "bestaudio/best",
             "--extract-audio", "--audio-format", "m4a",
             "-o", str(path), source_url(video_id)],
            capture_output=True, text=True, timeout=3600)
        if done.returncode == 0 and path.exists():
            return path
        path.unlink(missing_ok=True)
        last = ((done.stderr or "").strip().splitlines() or ["?"])[-1]
        raise RuntimeError(f"download failed: {last[:160]}")
    # Clients rotate per attempt, and the format selector falls back rather
    # than insisting. "bestaudio" alone is not always offered -- episode #49
    # refused it outright with "Requested format is not available" -- and
    # which client answers depends on what YouTube is challenging that day.
    # Same lesson as the clipper: vary the client, not just the retry.
    jar = None
    if COOKIES.is_file():
        import shutil
        jar = Path("/tmp/yt-cookies-ingest.txt")
        shutil.copyfile(COOKIES, jar)

    last = ""
    for client in ("tv_embedded", "", "web_embedded", "android"):
        cmd = [YTDLP, "--no-warnings",
               # Progressive audio, then any audio, then whatever exists.
               "-f", "bestaudio[ext=m4a]/bestaudio/best",
               "--extract-audio", "--audio-format", "m4a",
               "-o", str(path)]
        if client:
            cmd += ["--extractor-args", f"youtube:player_client={client}"]
        if jar:
            cmd += ["--cookies", str(jar)]
        cmd += [f"https://www.youtube.com/watch?v={video_id}"]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if done.returncode == 0 and path.exists():
            return path
        last = ((done.stderr or "").strip().splitlines() or ["?"])[-1]
        path.unlink(missing_ok=True)
    raise RuntimeError(f"download failed: {last[:160]}")


def transcribe(path: Path) -> list[dict]:
    import mlx_whisper
    result = mlx_whisper.transcribe(
        str(path), path_or_hf_repo="mlx-community/whisper-turbo",
        language="en", verbose=False)
    return [{"t": round(s["start"], 2), "text": s["text"].strip()}
            for s in result.get("segments", []) if s.get("text", "").strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="one video id")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    have = {e["episode_id"] for e in load()}
    todo = [s for s in SOURCES
            if (not args.only or s[0] == args.only) and s[0] not in have]

    if args.list:
        for vid, _channel, date, title in SOURCES:
            mark = "done" if vid in have else "    "
            print(f"  {mark}  {date}  {vid}  {title[:56]}")
        return 0

    print(f"  {len(have)} already in {OUT.name}, {len(todo)} to do\n")
    for vid, channel, date, title in todo:
        # A Space is admissible by construction: X hosts the one recording
        # on the account that opened it, so there is no re-upload for a
        # citation to land in by mistake.
        ok, why = (True, "") if len(vid) != 11 else admissible(channel, title)
        if not ok:
            print(f"  REFUSED {vid}: {why}")
            continue
        print(f"  {vid}  {title[:54]}", flush=True)
        try:
            audio = fetch_audio(vid)
            print(f"     {audio.stat().st_size // 1_000_000} MB, "
                  f"transcribing…", flush=True)
            segments = transcribe(audio)
        except Exception as exc:                            # noqa: BLE001
            print(f"     failed: {exc}")
            continue
        kept, dropped = drop_hallucinated(segments)
        if dropped:
            print(f"     dropped hallucinated: {', '.join(dropped)}")
        # Through the store, which takes an exclusive lock and re-reads
        # inside it. This runs for hours and something else may be writing;
        # a plain write_text here would silently drop whatever landed
        # between this script's last read and its next save.
        merge([{
            "episode_id": vid,
            "title": title,
            "url": source_url(vid),
            # "other" is what the Market Bubble broadcasts already use
            # for x.com; the schema has no separate X value and
            # inventing one fails validation at embed time.
            "platform": "youtube" if len(vid) == 11 else "other",
            "published_at": date,
            "channel": channel,
            "segments": kept,
        }], path=OUT)
        hours = max((s["t"] for s in kept), default=0) / 3600
        print(f"     {len(kept)} segments, {hours:.1f}h -> {OUT.name}\n",
              flush=True)

    total = load()
    hours = sum(max((s["t"] for s in e["segments"]), default=0)
                for e in total) / 3600
    print(f"  archive: {len(total)} episodes, {hours:.1f} hours")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

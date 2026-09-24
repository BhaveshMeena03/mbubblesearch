"""Bring the MCG Live archive up to date.

    .venv/bin/python scripts/ingest_mcg.py --list
    .venv/bin/python scripts/ingest_mcg.py
    .venv/bin/python scripts/ingest_mcg.py --only zJsQAfBROiQ

Two tabs, not one. The channel posts interviews to /videos and the daily
show to /streams, and an earlier pass read only /videos -- which is how
this archive once concluded MCG had four episodes when it had 225. The
missing set is computed against data/mcg_index.json by video id, so a
retitled upload is not re-ingested and a renamed tab cannot hide one.

Per episode: download the audio, transcribe it locally, embed the windows
into the `mcg` namespace, then add a row to the shelf. The row is written
LAST and only after the embedding returns, because the shelf is what
--list diffs against: a row saved before its vectors would mark an episode
done that nobody can search.

Transcripts are not kept. That is the existing design for this archive --
the shelf holds titles, urls and durations, the vectors hold the text, and
clips for MCG come from YouTube's own caption track. Keeping 1,000 hours
of MCG transcripts in the repo would double it for no query that needs
them.

Resumable. It is hours of laptop time, something will interrupt it, and
anything already in the shelf is skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from app.provenance import drop_hallucinated  # noqa: E402
from app.schemas import Episode  # noqa: E402

# This archive is NOT in the default index. It has its own -- mcg-search --
# and the namespace name is the same in both, which is exactly how a first
# run of this script put thirteen vectors somewhere nothing reads: the app
# builds its MCG index with index_name=mcg_pinecone_index, and anything
# that forgets that writes into the concierge's index instead.
SHELF = ROOT / "data" / "mcg_index.json"
AUDIO_DIR = Path("/tmp/mcg_audio")
YTDLP = next((str(p) for p in (ROOT / ".venv" / "bin" / "yt-dlp",)
              if p.is_file()), shutil.which("yt-dlp") or "yt-dlp")

# format, tab. The shelf already uses these two words, and the page groups
# on them, so they are not ours to rename.
TABS = [("interview", "https://www.youtube.com/@MCG_live/videos"),
        ("stream", "https://www.youtube.com/@MCG_live/streams")]

# A show is hours; a trailer is seconds. The shortest real episode in the
# shelf is around twelve minutes, so this only drops clips and shorts.
LONG_ENOUGH = 10 * 60


def proxy_args() -> list[str]:
    """--proxy for yt-dlp, or nothing when YTDLP_PROXY is unset.

    The same shape fetch_episodes uses, and the reason the first scheduled
    run of this job indexed nothing: the workflow passed YTDLP_PROXY in and
    this file had never heard of it. YouTube answered every download with
    "Sign in to confirm you're not a bot", each episode was caught and
    logged as a failure, and the job finished green in sixteen seconds
    having done nothing at all.

    Read at call time so a shell can set it without reimporting.

    The channel listing is deliberately NOT proxied -- it is not refused
    from a datacentre and a quiet run should spend no proxy bandwidth.
    Only fetching a video is.
    """
    proxy = os.environ.get("YTDLP_PROXY", "").strip()
    return ["--proxy", proxy] if proxy else []


def shelf() -> list[dict]:
    return json.loads(SHELF.read_text())


def enumerate_tab(url: str, limit: int) -> list[dict]:
    """Flat listing: ids, titles and durations, no dates and no cost."""
    done = subprocess.run(
        [YTDLP, "--flat-playlist", "-J", "--playlist-end", str(limit), url],
        capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        raise SystemExit(f"  could not list {url}: "
                         f"{(done.stderr or '').strip().splitlines()[-1:]}")
    return json.loads(done.stdout).get("entries", [])


def published(video_id: str) -> str:
    """YYYY-MM-DD, fetched per video because a flat listing has no date."""
    done = subprocess.run(
        [YTDLP, "--no-warnings", *proxy_args(), "--print", "%(upload_date)s",
         f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, timeout=300)
    raw = (done.stdout or "").strip()
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else ""


def pending(limit: int) -> list[dict]:
    have = {row["id"] for row in shelf()}
    found = []
    for fmt, url in TABS:
        for entry in enumerate_tab(url, limit):
            vid = entry.get("id")
            seconds = int(entry.get("duration") or 0)
            if not vid or vid in have or seconds < LONG_ENOUGH:
                continue
            found.append({"id": vid, "title": entry.get("title") or vid,
                          "seconds": seconds, "format": fmt})
            have.add(vid)
    return found


def fetch_audio(video_id: str) -> Path:
    AUDIO_DIR.mkdir(exist_ok=True)
    path = AUDIO_DIR / f"{video_id}.m4a"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    last = ""
    # Same client rotation as the Musk ingest: which one YouTube answers
    # depends on what it is challenging that day, so vary the client
    # rather than only the retry.
    for client in ("tv_embedded", "", "web_embedded", "android"):
        cmd = [YTDLP, "--no-warnings", *proxy_args(),
               "-f", "bestaudio[ext=m4a]/bestaudio/best",
               "--extract-audio", "--audio-format", "m4a", "-o", str(path)]
        if client:
            cmd += ["--extractor-args", f"youtube:player_client={client}"]
        cmd += [f"https://www.youtube.com/watch?v={video_id}"]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if done.returncode == 0 and path.exists():
            return path
        last = ((done.stderr or "").strip().splitlines() or ["?"])[-1]
        path.unlink(missing_ok=True)
    raise RuntimeError(f"download failed: {last[:160]}")


# Hosted transcription, for anywhere that is not this laptop.
#
# mlx_whisper is Apple silicon only, which is the whole reason MCG was
# never added to the twice-daily sync: the runner cannot import it. Groq
# serves the same family of model over HTTP, so a runner can do the work
# the laptop was doing.
#
# Chunked because the upload cap is 24MB and the median episode here is
# fifty minutes, with the longest at six hours. Downsampling to 16kHz mono
# -- which is what Whisper listens at anyway, so nothing is lost -- turns
# an hour of audio into about 15MB, and a fifteen-minute chunk into about
# 3.6MB. Well clear of the cap with room for a talkative episode.
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"
CHUNK_SECONDS = 15 * 60


def to_chunks(path: Path, work: Path) -> list[tuple[float, Path]]:
    """The episode as 16kHz mono mp3 pieces, each with its start time."""
    work.mkdir(parents=True, exist_ok=True)
    pattern = work / "chunk%04d.mp3"
    done = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path),
         "-ac", "1", "-ar", "16000", "-b:a", "32k",
         "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
         str(pattern)],
        capture_output=True, text=True, timeout=3600)
    if done.returncode != 0:
        raise RuntimeError(f"split failed: {(done.stderr or '')[-200:]}")
    return [(n * float(CHUNK_SECONDS), f)
            for n, f in enumerate(sorted(work.glob("chunk*.mp3")))]


def transcribe_via_groq(path: Path, api_key: str) -> list[dict]:
    """Segments with real timestamps, stitched back across the chunks.

    Every chunk's clock starts at zero, so each segment is shifted by where
    its chunk began. Getting that wrong does not fail -- it produces an
    episode whose every citation is silently minutes out, which is worse
    than no episode at all.
    """
    import httpx

    segments: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        chunks = to_chunks(path, Path(tmp))
        print(f"     {len(chunks)} chunk(s) to transcribe", flush=True)
        for offset, chunk in chunks:
            for attempt in range(6):
                try:
                    with open(chunk, "rb") as fh:
                        r = httpx.post(
                            GROQ_URL, timeout=600.0,
                            headers={"Authorization": f"Bearer {api_key}"},
                            files={"file": (chunk.name, fh, "audio/mpeg")},
                            data={"model": GROQ_MODEL,
                                  "response_format": "verbose_json",
                                  "timestamp_granularities[]": "segment",
                                  "language": "en", "temperature": "0"})
                    # The free tier's hourly allowance is smaller than a
                    # day of this show. A batch job can wait; it is the
                    # only caller that can.
                    if r.status_code == 429:
                        wait = float(r.headers.get("retry-after") or 30)
                        print(f"     rate limited, waiting {wait:.0f}s",
                              flush=True)
                        time.sleep(min(wait, 120))
                        continue
                    r.raise_for_status()
                    body = r.json() or {}
                except Exception as exc:                    # noqa: BLE001
                    if attempt == 5:
                        raise RuntimeError(f"transcribe failed: {exc}") from exc
                    time.sleep(5 * (attempt + 1))
                    continue
                for seg in body.get("segments", []):
                    said = (seg.get("text") or "").strip()
                    if said:
                        segments.append({"t": round(offset + float(
                            seg.get("start") or 0.0), 2), "text": said})
                break
    segments.sort(key=lambda s: s["t"])
    return segments


def transcribe(path: Path) -> list[dict]:
    """Locally when this machine can, hosted when it cannot.

    The laptop keeps using MLX -- it is free and already downloaded. A
    runner has no MLX and a GROQ_API_KEY instead, and takes the other road
    without the caller knowing which one it got.
    """
    try:
        import mlx_whisper
    except ImportError:
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "no transcriber: mlx_whisper will not import here and "
                "GROQ_API_KEY is unset") from None
        return transcribe_via_groq(path, key)

    result = mlx_whisper.transcribe(
        str(path), path_or_hf_repo="mlx-community/whisper-turbo",
        language="en", verbose=False)
    return [{"t": round(s["start"], 2), "text": s["text"].strip()}
            for s in result.get("segments", []) if s.get("text", "").strip()]


def add_to_shelf(row: dict) -> None:
    """Newest first, by date rather than by arrival.

    Prepending looked the same until a batch finished: episodes land in
    whatever order they transcribe, so the file's first row was the last
    one ingested, not the most recent show. Nothing reads the order --
    main.py sorts on published_at -- but a file that claims an order
    should keep it.
    """
    rows = [r for r in shelf() if r["id"] != row["id"]] + [row]
    rows.sort(key=lambda r: (r.get("published_at") or "", r["id"]),
              reverse=True)
    SHELF.write_text(json.dumps(rows, indent=1))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show what is missing")
    ap.add_argument("--only", help="one video id")
    ap.add_argument("--limit", type=int, default=60,
                    help="how deep to read each tab")
    # A cap on WORK, which --limit is not: that one says how far back to
    # look, and a sync that has not run for a week would happily find
    # twenty episodes and try to transcribe all of them. On a runner with
    # a six-hour ceiling that is a job which never finishes and never
    # records what it did. Newest first, the rest next time.
    ap.add_argument("--max-new", type=int, default=0,
                    help="at most N new episodes this run (0 = no cap)")
    args = ap.parse_args()

    todo = pending(args.limit)
    if args.only:
        todo = [t for t in todo if t["id"] == args.only]
    capped = 0
    if args.max_new and len(todo) > args.max_new:
        capped = len(todo) - args.max_new
        todo = todo[:args.max_new]

    hours = sum(t["seconds"] for t in todo) / 3600
    print(f"  {len(shelf())} episodes on the shelf, {len(todo)} to add "
          f"({hours:.1f} hours of audio)"
          + (f", {capped} left for the next run" if capped else "") + "\n")
    for t in todo:
        print(f"  {t['id']}  {t['seconds'] // 60:4}m  {t['format']:9} "
              f"{t['title'][:52]}")
    if args.list or not todo:
        return 0

    settings = get_settings()
    index = PodcastIndex(namespace=settings.mcg_namespace,
                         index_name=settings.mcg_pinecone_index)
    print(f"  writing to index {settings.mcg_pinecone_index!r}, "
          f"namespace {settings.mcg_namespace!r}")
    failures: list[str] = []
    added = 0
    for n, item in enumerate(todo, 1):
        print(f"\n  [{n}/{len(todo)}] {item['id']}  {item['title'][:54]}",
              flush=True)
        try:
            audio = fetch_audio(item["id"])
            print(f"     {audio.stat().st_size // 1_000_000} MB, "
                  f"transcribing…", flush=True)
            segments = transcribe(audio)
        except Exception as exc:                            # noqa: BLE001
            print(f"     failed: {exc}")
            failures.append(f"{item['id']}: {str(exc)[:120]}")
            continue
        kept, dropped = drop_hallucinated(segments)
        if dropped:
            print(f"     dropped hallucinated: {', '.join(dropped)}")
        date = published(item["id"])
        episode = Episode(
            episode_id=item["id"], title=item["title"],
            url=f"https://www.youtube.com/watch?v={item['id']}",
            platform="youtube", published_at=date or None,
            segments=kept,
        )
        windows = await index.ingest([episode])
        print(f"     {len(kept)} segments -> {windows} searchable passages",
              flush=True)
        add_to_shelf({"id": item["id"], "title": item["title"],
                      "url": episode.url, "published_at": date,
                      "seconds": item["seconds"], "format": item["format"]})
        added += 1
        audio.unlink(missing_ok=True)

    rows = shelf()
    print(f"\n  shelf: {len(rows)} episodes, "
          f"{sum(r['seconds'] for r in rows) / 3600:.1f} hours")

    # Having work and completing none of it is a failure, and it has to
    # say so. The first scheduled run of this found three episodes, was
    # refused by YouTube on all three, caught each one, and exited green
    # in sixteen seconds -- which is indistinguishable, from the outside,
    # from an archive that was already up to date.
    if todo and not added:
        print(f"\n  indexed NOTHING out of {len(todo)} episode(s):")
        for line in failures:
            print(f"    {line}")
        return 1
    if failures:
        print(f"\n  {len(failures)} of {len(todo)} failed, "
              f"{added} indexed; the rest will retry next run")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

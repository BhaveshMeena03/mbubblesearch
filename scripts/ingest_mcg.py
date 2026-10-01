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
import concurrent.futures as cf
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
# One pipeline, more than one channel. Threadguy is the same shape of
# source as MCG -- a daily long stream plus shorter uploads, on two tabs,
# no transcripts worth keeping in the repo -- so it gets the same code
# rather than a fork that drifts. --archive picks the set; the default is
# mcg, so every existing invocation and the scheduled job are unchanged.
ARCHIVES = {
    "mcg": {
        "shelf": ROOT / "data" / "mcg_index.json",
        "audio": Path("/tmp/mcg_audio"),
        "handle": "@MCG_live",
        "namespace": lambda s: s.mcg_namespace,
        "index": lambda s: s.mcg_pinecone_index,
    },
    "threadguy": {
        "shelf": ROOT / "data" / "threadguy_index.json",
        "audio": Path("/tmp/threadguy_audio"),
        "handle": "@notthreadguy",
        # Its own namespace in the default index rather than an index of
        # its own. The 181 vectors already sitting there were built from
        # YouTube's auto-captions, and the page on threadguy.lexthedev.com
        # reads them, so writing Whisper passages into the same place
        # upgrades that page instead of orphaning it.
        "namespace": lambda s: "threadguy",
        "index": lambda s: s.pinecone_index,
    },
}
ARCHIVE = "mcg"

SHELF = ARCHIVES["mcg"]["shelf"]
AUDIO_DIR = ARCHIVES["mcg"]["audio"]
YTDLP = next((str(p) for p in (ROOT / ".venv" / "bin" / "yt-dlp",)
              if p.is_file()), shutil.which("yt-dlp") or "yt-dlp")

# format, tab. The shelf already uses these two words, and the page groups
# on them, so they are not ours to rename.
TABS = [("interview", "https://www.youtube.com/@MCG_live/videos"),
        ("stream", "https://www.youtube.com/@MCG_live/streams")]


def use(archive: str) -> dict:
    """Point the module at one channel. Called once, before any work."""
    global SHELF, AUDIO_DIR, TABS, ARCHIVE
    spec = ARCHIVES[archive]
    ARCHIVE = archive
    SHELF = spec["shelf"]
    AUDIO_DIR = spec["audio"]
    TABS = [("interview", f"https://www.youtube.com/{spec['handle']}/videos"),
            ("stream", f"https://www.youtube.com/{spec['handle']}/streams")]
    return spec

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
    """Rows already ingested, or nothing on the first run of an archive.

    MCG's shelf has existed since before this script did, so the missing
    file was never a case until a second archive was added and --list
    crashed on it.
    """
    if not SHELF.exists():
        return []
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
    """YYYY-MM-DD the episode went out, fetched per video because a flat
    listing has no date.

    The air date, not the upload date. For a past live stream YouTube's
    upload_date is when the recording finished processing, which for a
    market open that ends in the US afternoon is often the next day in
    UTC: ThreadGuy's Robinhood Summit stream aired 30 Sep and came back
    as 1 Oct. release_date is when it aired. An ordinary upload has none,
    or the same day, so it falls back to upload_date.
    """
    done = subprocess.run(
        [YTDLP, "--no-warnings", *proxy_args(), "--print",
         "%(release_date)s %(upload_date)s",
         f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, timeout=300)
    return air_date(done.stdout or "")


def air_date(printed: str) -> str:
    """The first real YYYYMMDD in "release upload", as YYYY-MM-DD."""
    for raw in printed.split():
        if len(raw) == 8 and raw.isdigit():
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


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

        # The proxy negotiates TLS the old way and yt-dlp refuses it:
        # "SSLV3_ALERT_HANDSHAKE_FAILURE: The server may not support the
        # current cipher list." Retried once with the flag its own error
        # message names, rather than weakening every request to suit one
        # hop -- the first attempt stays strict and this only runs when
        # that attempt has already failed on the handshake.
        if "HANDSHAKE_FAILURE" in last or "cipher list" in last:
            done = subprocess.run([*cmd, "--legacy-server-connect"],
                                  capture_output=True, text=True, timeout=7200)
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
# How many chunks of one episode are in flight at once, and which
# transcriber to use. Both are set from the command line; the defaults
# keep every existing invocation, including the scheduled sync, on
# exactly the path it was on before.
CHUNK_WORKERS = 1
TRANSCRIBER = "auto"


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


class RateLimitedError(RuntimeError):
    """Groq kept saying come back later, for longer than a run should wait."""


# Waits of up to five minutes each: about an hour of patience per chunk,
# which is how long the free tier's hourly allowance takes to come back.
MAX_REFUSALS = 12


def transcribe_via_groq(path: Path, api_key: str) -> list[dict]:
    """Segments with real timestamps, stitched back across the chunks.

    Every chunk's clock starts at zero, so each segment is shifted by where
    its chunk began. Getting that wrong does not fail -- it produces an
    episode whose every citation is silently minutes out, which is worse
    than no episode at all.
    """
    import httpx

    def one(offset: float, chunk: Path) -> list[dict]:
        """One chunk, with its own retries, already on the episode clock.

        Raises rather than returning nothing. It used to fall out of the
        retry loop with an empty list once Groq had refused it six times,
        and the episode was shelved with that hole in it: fourteen
        ThreadGuy streams went up with minutes, or nothing, transcribed.
        """
        errors = refusals = 0
        while True:
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
                # The free tier's hourly allowance is smaller than a day
                # of this show. A batch job can wait; it is the only
                # caller that can. Refusals get their own, longer budget:
                # they say when to come back, an error does not.
                if r.status_code == 429:
                    refusals += 1
                    if refusals > MAX_REFUSALS:
                        raise RateLimitedError(
                            f"still rate limited after {MAX_REFUSALS} waits")
                    wait = float(r.headers.get("retry-after") or 30)
                    print(f"     rate limited, waiting {min(wait, 300):.0f}s",
                          flush=True)
                    time.sleep(min(wait, 300))
                    continue
                r.raise_for_status()
                body = r.json() or {}
            except RateLimitedError:
                raise
            except Exception as exc:                        # noqa: BLE001
                errors += 1
                if errors >= 6:
                    raise RuntimeError(f"transcribe failed: {exc}") from exc
                time.sleep(5 * errors)
                continue
            out: list[dict] = []
            for seg in body.get("segments", []):
                said = (seg.get("text") or "").strip()
                if said:
                    out.append({"t": round(offset + float(
                        seg.get("start") or 0.0), 2), "text": said})
            return out

    segments: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        chunks = to_chunks(path, Path(tmp))
        print(f"     {len(chunks)} chunk(s) to transcribe"
              + (f", {CHUNK_WORKERS} at a time" if CHUNK_WORKERS > 1 else ""),
              flush=True)
        # Each chunk carries its own offset, so they may come back in any
        # order without putting a citation in the wrong minute. The sort
        # below is what makes the order irrelevant.
        if CHUNK_WORKERS > 1 and len(chunks) > 1:
            with cf.ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as pool:
                for got in pool.map(lambda c: one(*c), chunks):
                    segments.extend(got)
        else:
            for offset, chunk in chunks:
                segments.extend(one(offset, chunk))
    segments.sort(key=lambda s: s["t"])
    return segments



def groq_key() -> str:
    """The environment's key, else the one in .env, like every other key.

    Reading only the environment meant the 09:00 job, which launchd starts
    with an empty one, could never transcribe: it would have failed every
    morning from its first real run.
    """
    return (os.environ.get("GROQ_API_KEY", "").strip()
            or (get_settings().groq_api_key or "").strip())


def transcribe(path: Path) -> list[dict]:
    """Locally when this machine can, hosted when it cannot.

    The laptop keeps using MLX -- it is free and already downloaded. A
    runner has no MLX and a GROQ_API_KEY instead, and takes the other road
    without the caller knowing which one it got.

    --transcriber groq overrides that. MLX transcribes one episode at a
    time on one GPU, which is free but fixes the rate at roughly half an
    episode a minute: fine for the nightly handful, twenty hours for a
    channel arriving all at once. Groq runs the same model family over
    HTTP, so the chunks of an episode can be in flight together.
    """
    if TRANSCRIBER == "groq":
        key = groq_key()
        if not key:
            raise RuntimeError("--transcriber groq needs GROQ_API_KEY")
        return transcribe_via_groq(path, key)
    try:
        import mlx_whisper
    except ImportError:
        key = groq_key()
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


def shortfall(segments: list[dict], seconds: float) -> str | None:
    """Why a transcript is too short to shelve, or None if it is whole.

    Missing more than ten minutes AND more than a fifth of the recording.
    Either alone is normal: a stream can end on a quiet minute, and a short
    clip can end ten seconds early.
    """
    if not segments:
        return "no transcript"
    if not seconds:
        return None
    missing = float(seconds) - float(segments[-1]["t"])
    if missing > 600 and missing > 0.2 * float(seconds):
        return (f"transcript stops at {segments[-1]['t'] / 60:.0f} of "
                f"{float(seconds) / 60:.0f} minutes")
    return None


def forget(video_id: str) -> int:
    """Take an episode off the shelf and out of the index, so it is done
    again from scratch. Returns how many passages were removed.

    The passages go by id, read back with a filter on the episode: a
    re-transcription windows differently, so leaving the old ones would
    keep fragments of the broken copy beside the new one.
    """
    settings = get_settings()
    spec = ARCHIVES[ARCHIVE]
    ns = spec["namespace"](settings)
    index = PodcastIndex(namespace=ns, index_name=spec["index"](settings)).index
    probe = [0.0] * settings.embedding_dimension
    probe[0] = 1.0
    found = index.query(vector=probe, top_k=1000, namespace=ns,
                        include_metadata=False,
                        filter={"episode_id": {"$eq": video_id}})
    ids = [m["id"] for m in found.get("matches", [])]
    for at in range(0, len(ids), 100):
        index.delete(ids=ids[at:at + 100], namespace=ns)
    rows = [r for r in shelf() if r.get("id") != video_id]
    SHELF.write_text(json.dumps(rows, indent=1))
    return len(ids)


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
    ap.add_argument("--archive", default="mcg", choices=sorted(ARCHIVES),
                    help="which channel to bring up to date")
    ap.add_argument("--list", action="store_true", help="show what is missing")
    ap.add_argument("--only", help="one video id")
    ap.add_argument("--redo", action="append", default=[], metavar="ID",
                    help="delete an episode's passages and shelf row, then "
                         "ingest it again (repeatable)")
    ap.add_argument("--limit", type=int, default=60,
                    help="how deep to read each tab")
    ap.add_argument("--transcriber", default="auto",
                    choices=("auto", "groq", "local"),
                    help="auto uses MLX here and Groq on a runner; groq "
                         "forces the hosted path, which is the only one "
                         "that can do more than one thing at a time")
    ap.add_argument("--chunk-workers", type=int, default=1,
                    help="chunks of one episode in flight at once (groq)")
    ap.add_argument("--workers", type=int, default=1,
                    help="episodes downloaded and transcribed at once")
    # A cap on WORK, which --limit is not: that one says how far back to
    # look, and a sync that has not run for a week would happily find
    # twenty episodes and try to transcribe all of them. On a runner with
    # a six-hour ceiling that is a job which never finishes and never
    # records what it did. Newest first, the rest next time.
    ap.add_argument("--max-new", type=int, default=0,
                    help="at most N new episodes this run (0 = no cap)")
    args = ap.parse_args()
    global CHUNK_WORKERS, TRANSCRIBER
    CHUNK_WORKERS = max(1, args.chunk_workers)
    TRANSCRIBER = args.transcriber
    if TRANSCRIBER == "local":
        TRANSCRIBER = "auto"
    spec = use(args.archive)

    # A redo comes from the shelf row, not the channel listing: the
    # broken episodes are the old ones as often as the new, further back
    # than --limit reads, and the row already has everything needed.
    if args.redo:
        rows = {r["id"]: r for r in shelf()}
        missing = [v for v in args.redo if v not in rows]
        if missing:
            print(f"  not on the shelf: {', '.join(missing)}")
            return 1
        todo = [{"id": v, "title": rows[v]["title"],
                 "seconds": int(rows[v]["seconds"]),
                 "format": rows[v]["format"]} for v in args.redo]
        if args.list:
            for t in todo:
                print(f"  would redo {t['id']}  {t['seconds'] // 60}m  "
                      f"{t['title'][:52]}")
            return 0
        for t in todo:
            print(f"  {t['id']}: removed {forget(t['id'])} old passages "
                  f"and its shelf row", flush=True)
    else:
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
    ns, ix = spec["namespace"](settings), spec["index"](settings)
    index = PodcastIndex(namespace=ns, index_name=ix)
    print(f"  writing to index {ix!r}, namespace {ns!r}")
    failures: list[str] = []
    added = 0
    # Downloading and transcribing is network and CPU and belongs off the
    # event loop; writing to the index and to the shelf stays on it, one
    # episode at a time. Two writers on that JSON file would silently
    # drop whichever row lost, and a lost row is an episode that is in
    # the index but that nothing will ever list.
    def prepare(item: dict) -> tuple[dict, list[dict], str]:
        audio = fetch_audio(item["id"])
        print(f"     {item['id']}: {audio.stat().st_size // 1_000_000} MB, "
              f"transcribing…", flush=True)
        try:
            segments = transcribe(audio)
        finally:
            audio.unlink(missing_ok=True)
        kept, dropped = drop_hallucinated(segments)
        if dropped:
            print(f"     {item['id']}: dropped hallucinated: "
                  f"{', '.join(dropped)}")
        return item, kept, published(item["id"])

    workers = max(1, args.workers)
    loop = asyncio.get_running_loop()
    pool = cf.ThreadPoolExecutor(max_workers=workers)
    gate = asyncio.Semaphore(workers)

    async def ready(item: dict):
        async with gate:
            return await loop.run_in_executor(pool, prepare, item)

    jobs = [asyncio.ensure_future(ready(t)) for t in todo]
    n = 0
    try:
        for job in asyncio.as_completed(jobs):
            n += 1
            try:
                item, kept, date = await job
            except Exception as exc:                        # noqa: BLE001
                print(f"  [{n}/{len(todo)}] failed: {exc}")
                failures.append(str(exc)[:120])
                continue
            # A transcript that stops well short of the recording is not
            # an episode, whatever caused it: chunks lost to rate limits,
            # or a stream fetched while YouTube was still processing it.
            # Fourteen were shelved like that, eleven with nothing at all.
            short = shortfall(kept, item["seconds"])
            if short:
                print(f"  [{n}/{len(todo)}] {item['id']} {short}; not shelved",
                      flush=True)
                failures.append(f"{item['id']}: {short}")
                continue
            episode = Episode(
                episode_id=item["id"], title=item["title"],
                url=f"https://www.youtube.com/watch?v={item['id']}",
                platform="youtube", published_at=date or None,
                segments=kept,
            )
            # Embedding is a network call and it was the only step in the
            # loop that could not fail politely. One transient
            # APIConnectionError from Voyage took down a 607 episode run
            # at 394, discarding every episode still in flight. A blip
            # now costs the one episode, which is not shelved and so is
            # picked up by the next run.
            try:
                windows = await index.ingest([episode])
            except Exception as exc:                        # noqa: BLE001
                print(f"  [{n}/{len(todo)}] {item['id']} embed failed: "
                      f"{str(exc)[:90]}", flush=True)
                failures.append(f"{item['id']}: embed {str(exc)[:90]}")
                continue
            print(f"  [{n}/{len(todo)}] {item['id']}  "
                  f"{item['title'][:44]}  {len(kept)} segments -> "
                  f"{windows} passages", flush=True)
            add_to_shelf({"id": item["id"], "title": item["title"],
                          "url": episode.url, "published_at": date,
                          "seconds": item["seconds"], "format": item["format"]})
            added += 1
    finally:
        for job in jobs:
            job.cancel()
        pool.shutdown(wait=False)

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

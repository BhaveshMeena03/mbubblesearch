"""Read the Polymarket ticker off a Market Bubble broadcast.

    .venv/bin/python scripts/polymarket_ticker.py x-2118457515969032365
    .venv/bin/python scripts/polymarket_ticker.py --every 10 x-2108360500283335093 ...

Runs on the laptop: X's replay is reachable from here and Polymarket is
not, so this only reads the video and app/polymarket.py's matching runs
later on a GitHub runner (scripts/polymarket_match.py).

For each episode it takes one two-second segment in every --every (ten, so
a frame every twenty seconds), crops the ticker and the corner clock, and
reads both with tesseract. The ticker loops about every three and a half
minutes, so a frame every twenty seconds sees each market about ten times
an hour, for a tenth of the download: about 1.3 GB for a four-hour show
rather than 13.

When a frame aired comes from the replay's own playlist. The corner clock
is read on every third frame and must agree with it; if it does not, the
episode is not written, because every time on the page would be wrong.

Writes data/polymarket/ticker/<episode id>.json. Needs yt-dlp (in the
venv), ffmpeg and tesseract (Homebrew).
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import polymarket as pm  # noqa: E402

OUT = ROOT / "data" / "polymarket" / "ticker"
BROADCASTS = ROOT / "data" / "broadcast_links.json"
EPISODES = ROOT / "data" / "episodes.json.gz"
YTDLP = Path(sys.executable).with_name("yt-dlp")
# Every third sampled frame also has its clock read: about one a minute.
CLOCK_EVERY = 3
# A replay missing more than this share of its sampled segments is not
# written: the gaps would hide markets.
MAX_MISSING = 0.05
# Where in a two-second segment the frame is taken: its first frame. A
# segment is cut on a keyframe, and seeking inside one (-ss 1.0) gave
# ffmpeg nothing to decode and no picture, without an error.
FRAME_AT = 0.0


def replay_url(broadcast: str) -> str:
    """The HLS playlist behind an X broadcast."""
    got = subprocess.run([str(YTDLP), "-g", broadcast], capture_output=True,
                         text=True, timeout=120)
    url = got.stdout.strip().splitlines()[0] if got.stdout.strip() else ""
    if not url.startswith("http"):
        raise RuntimeError(f"yt-dlp found no replay for {broadcast}: "
                           f"{got.stderr.strip()[-300:]}")
    return url


def fetch(url: str, dest: Path | None = None) -> bytes:
    """A URL's body, by curl.

    curl rather than urllib: on 10 Nov 2026 urllib sat in a TCP connect to
    X's CDN for minutes at a time on this connection, where curl, with a
    connect timeout and its own retries, came straight back.
    """
    out = dest or Path(tempfile.mkstemp(prefix="pmfetch-")[1])
    try:
        done = subprocess.run(
            ["curl", "-sS", "--fail", "--connect-timeout", "10", "--max-time", "90",
             "--retry", "4", "--retry-delay", "2", "--retry-all-errors",
             "-A", "Mozilla/5.0", "-o", str(out), url],
            capture_output=True, text=True, timeout=600)
        if done.returncode != 0:
            raise RuntimeError(f"curl failed ({done.returncode}): {done.stderr.strip()[-200:]}")
        return b"" if dest else out.read_bytes()
    finally:
        if dest is None:
            out.unlink(missing_ok=True)


def crop(box: tuple[int, int, int, int]) -> str:
    x, y, w, h = box
    # Three times the size, grey and inverted: tesseract reads dark text on
    # light, and the ticker is light text on dark.
    return f"crop={w}:{h}:{x}:{y},scale=iw*3:ih*3,format=gray,negate"


def frames(segment: Path, band: Path, clock: Path | None) -> bool:
    """Cut the ticker (and the clock, twice) out of one segment. False when
    the segment would not decode.

    The clock is cut two ways, plain and thresholded, because each one
    reads some frames the other misreads.
    """
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(segment)]
    if clock is None:
        cmd += ["-frames:v", "1", "-vf", crop(pm.TICKER_BOX), str(band)]
    else:
        x, y, w, h = pm.CLOCK_BOX
        sharp = (f"crop={w}:{h}:{x}:{y},scale=iw*4:ih*4:flags=lanczos,format=gray,"
                 f"negate,lutyuv=y='if(lt(val,140),0,255)'")
        cmd += ["-filter_complex",
                f"[0:v]split=3[a][b][c];[a]{crop(pm.TICKER_BOX)}[t];"
                f"[b]{crop(pm.CLOCK_BOX)}[c1];[c]{sharp}[c2]",
                "-map", "[t]", "-frames:v", "1", str(band),
                "-map", "[c1]", "-frames:v", "1", str(clock),
                "-map", "[c2]", "-frames:v", "1", str(sharpened(clock))]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return done.returncode == 0 and band.exists()


def sharpened(clock: Path) -> Path:
    return clock.with_name(clock.stem + "s.png")


def ocr(images: list[Path], work: Path, name: str, psm: str = "7",
        extra: tuple[str, ...] = ()) -> list[str]:
    """Text of each image, in order, in one tesseract run."""
    if not images:
        return []
    listing = work / f"{name}.txt"
    listing.write_text("\n".join(str(p) for p in images) + "\n")
    out = work / f"{name}_out"
    subprocess.run(["tesseract", str(listing), str(out), "--psm", psm, *extra],
                   capture_output=True, text=True, timeout=3600, check=True)
    pages = (work / f"{name}_out.txt").read_text(errors="replace").split("\f")
    # tesseract ends the last page with a form feed too.
    if len(pages) == len(images) + 1 and not pages[-1].strip():
        pages = pages[:-1]
    if len(pages) != len(images):
        raise RuntimeError(f"tesseract gave {len(pages)} pages for {len(images)} images")
    return [" ".join(p.split()) for p in pages]


def episode_meta(episode_id: str) -> dict:
    try:
        rows = json.loads(gzip.decompress(EPISODES.read_bytes()))
    except (OSError, ValueError):
        return {}
    for row in rows:
        if row.get("episode_id") == episode_id:
            return {"title": row.get("title"), "published_at": row.get("published_at")}
    return {}


def read_episode(episode_id: str, every: int, work: Path) -> dict:
    links = json.loads(BROADCASTS.read_text())
    broadcast = links.get(episode_id)
    if not broadcast:
        raise SystemExit(f"{episode_id} has no X broadcast in {BROADCASTS.name}")
    url = replay_url(broadcast)
    base = url.rsplit("/", 1)[0]
    segments = pm.playlist(fetch(url).decode())
    picked = pm.sample(segments, every)
    print(f"{episode_id}: {len(segments)} segments, reading {len(picked)}", flush=True)

    bands, clocks, kept, missing = [], [], [], 0
    for i, seg in enumerate(picked):
        local = work / "seg.ts"
        try:
            fetch(f"{base}/{seg.uri}", local)
        except RuntimeError:
            missing += 1
            continue
        band = work / f"b{i:05d}.png"
        clock = work / f"c{i:05d}.png" if i % CLOCK_EVERY == 0 else None
        if not frames(local, band, clock):
            missing += 1
            continue
        bands.append(band)
        kept.append(seg)
        if clock is not None and clock.exists() and sharpened(clock).exists():
            clocks.append((seg, clock))
        if i and i % 100 == 0:
            print(f"  {i}/{len(picked)}", flush=True)
    if missing > MAX_MISSING * len(picked):
        raise SystemExit(f"{episode_id}: {missing} of {len(picked)} segments failed; not written")

    # The time first: if the clock disagrees, nothing else matters.
    # The clock is two lines, the date over the time.
    plain = ocr([c for _, c in clocks], work, "clock", psm="6")
    sharp = ocr([sharpened(c) for _, c in clocks], work, "clocks", psm="6",
                extra=("-c", "tessedit_char_whitelist=0123456789:/APMESTD "))
    offsets = []
    for (seg, _), texts in zip(clocks, zip(plain, sharp, strict=True), strict=True):
        aired = seg.aired + timedelta(seconds=FRAME_AT)
        got = [pm.clock_offset(aired, c) for c in map(pm.read_clock, texts) if c]
        if got:
            known = [o for o in got if o is not None]
            offsets.append(min(known, key=abs) if known else None)
    near = sum(o is not None and -1 <= o <= 1 for o in offsets)
    if not pm.clock_holds(offsets):
        raise SystemExit(f"{episode_id}: the corner clock agreed on {near} of {len(offsets)} "
                         f"readable frames; the timing cannot be trusted, not written")

    sightings = []
    with_ticker = 0
    for seg, text in zip(kept, ocr(bands, work, "band"), strict=True):
        found = pm.readings(text)
        with_ticker += bool(found)
        for r in found:
            sightings.append(pm.Sighting(t=round(seg.start + FRAME_AT, 1),
                                         aired=seg.aired + timedelta(seconds=FRAME_AT),
                                         yes=r.yes, text=r.text, whole=r.whole))
    markets = pm.group(sightings)
    print(f"  clock {near}/{len(offsets)} agree; {with_ticker}/{len(kept)} frames show the "
          f"ticker; {len(sightings)} readings in {len(markets)} markets", flush=True)
    last = segments[-1]
    return {
        "episode_id": episode_id,
        **episode_meta(episode_id),
        "broadcast": broadcast,
        "aired": segments[0].aired.isoformat(),
        "ended": (last.aired + timedelta(seconds=last.duration)).isoformat(),
        "every": every,
        "frames": len(kept),
        "frames_with_ticker": with_ticker,
        "clock": {"read": len(clocks), "parsed": len(offsets), "agree": near},
        "markets": [{"text": g.text,
                     "sightings": [[s.t, s.aired.isoformat(), s.yes, s.whole, s.text]
                                   for s in g.sightings]}
                    for g in markets],
        "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("episodes", nargs="+", help="Market Bubble episode ids (x-...)")
    ap.add_argument("--every", type=int, default=10,
                    help="read one segment in this many (default 10, a frame every 20s)")
    args = ap.parse_args()
    for tool in ("ffmpeg", "tesseract"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is not on PATH")
    OUT.mkdir(parents=True, exist_ok=True)
    for episode_id in args.episodes:
        work = Path(tempfile.mkdtemp(prefix="pmticker-"))
        try:
            got = read_episode(episode_id, args.every, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        # Named for what it is: the ticker file, never the episode file,
        # which this script only reads.
        ticker_file = OUT / f"{episode_id}.json"
        ticker_file.write_text(json.dumps(got, separators=(",", ":"), ensure_ascii=False) + "\n")
        print(f"  wrote {ticker_file.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()

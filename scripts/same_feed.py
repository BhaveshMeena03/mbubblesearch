"""Ansem's copy of a Market Bubble broadcast: is it the same feed, and
where does its clock sit against the episode the archive holds?

Ansem broadcasts every Market Bubble episode from his own account at the
moment Market Bubble does: 23 of 24 since May 2026 were posted within 40
seconds of Market Bubble's, each under a broadcast id of its own. The
archive indexes a show once, from Market Bubble's post, so the copy on
the bigger of the two profiles had no button.

Where: X's replay playlist stamps every two-second chunk with the time it
went out. Both copies of one feed carry the same times, so the second on
his that went out with a second on theirs is read off the two playlists,
with nothing downloaded but them. Episode 24's showed what one reading of
the sound could not: Market Bubble's replay lost 62 seconds at 2:23:13
that Ansem's kept, so every time after it is 63 seconds later on his.

Whether: the stamps would line up just as well for something else he
broadcast at that hour, so the sound has the last word. A stretch of
theirs is looked for in his by its loudness, a hundred readings a second
(never its words: nothing is transcribed, and nothing goes anywhere but
X's video servers), at the second the stamps say. Episode 24 lined up at
0.999; the best any other stretch of it managed was 0.23.

    .venv/bin/python scripts/same_feed.py COPY_POST SHOW_POST
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
YT_DLP = ROOT / ".venv" / "bin" / "yt-dlp"

MAX_PLAYLIST_BYTES = 8 * 1024 * 1024    # Episode 24's is 0.7 MB for three hours
SMOOTH = 9                  # chunks a reading is the median of, about 18 s
AGREE_SECONDS = 1.5
MIN_STRETCH = 30            # shorter than this, a stretch is noise in the stamps
MIN_SHARED = 600            # less of the show than this in common is no copy

RATE = 8000                 # audio samples a second read off X
HOP = 80                    # so a hundred loudness readings a second
PROBE_SECONDS = 40          # how much of the show is looked for in the copy
REACH_SECONDS = 10          # how far from the stamps' second it is looked for
SAME = 0.8                  # the same feed lines up at 0.99, another stretch 0.3
CLEAR = 0.3                 # and nowhere else may come within this of it
CHECKS = 3                  # places the sound is tried before giving up
CHECK_AGREE = 2.0


def stream(post: str) -> tuple[str, str] | None:
    """(broadcast id, address of its smallest stream) for a post, or None.
    The smallest, since only the sound is wanted: Episode 24's 1080p
    stream took two minutes a reading, and one reading never finished."""
    try:
        run = subprocess.run(
            [str(YT_DLP), "--socket-timeout", "30", "--no-warnings", "--playlist-items", "1",
             "-f", "worst", "--print", "id", "-g", f"https://x.com/i/status/{post}"],
            capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None
    lines = run.stdout.strip().splitlines()
    if run.returncode != 0 or len(lines) < 2 or not lines[-1].startswith("https://"):
        return None
    return lines[-2], lines[-1]


def parse(playlist: str) -> list[tuple[float, float, float]] | None:
    """(position, time it went out, length) of every chunk in a replay's
    playlist, or None unless every one of them is stamped."""
    out: list[tuple[float, float, float]] = []
    position, wall = 0.0, None
    for line in playlist.splitlines():
        line = line.strip()
        try:
            if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
                wall = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00")).timestamp()
            elif line.startswith("#EXTINF:"):
                length = float(line[len("#EXTINF:"):].split(",")[0])
                if wall is None or not 0 < length < 60:
                    return None
                out.append((position, wall, length))
                position, wall = position + length, None
        except ValueError:
            return None
    return out or None


def chunks(media: str) -> list[tuple[float, float, float]] | None:
    try:
        with urllib.request.urlopen(media, timeout=60) as reply:      # noqa: S310 (yt-dlp's https)
            body = reply.read(MAX_PLAYLIST_BYTES + 1)
    except (OSError, ValueError):
        return None
    if len(body) > MAX_PLAYLIST_BYTES:
        return None
    return parse(body.decode("utf-8", "replace"))


def _smoothed(values: list[float]) -> list[float]:
    """Each value as the median of those around it: a lone chunk stamped
    two seconds out is not a lost stretch. Ansem's Episode 24 has six."""
    half = SMOOTH // 2
    return [float(np.median(values[max(0, i - half):i + half + 1])) for i in range(len(values))]


def stretches(chunks: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """(when it began going out, when it stopped, stamp minus position) for
    each unbroken stretch of a replay. Within one, every chunk is stamped
    its position plus the same number, to a hundredth over three hours;
    a lost stretch moves the number for good."""
    gaps = [wall - position for position, wall, _ in chunks]
    smooth = _smoothed(gaps)
    runs: list[list[int]] = []
    for i, value in enumerate(smooth):
        if runs and abs(value - smooth[runs[-1][0]]) <= AGREE_SECONDS:
            runs[-1].append(i)
        else:
            runs.append([i])
    out = []
    for run in runs:
        gap = float(np.median([gaps[i] for i in run]))
        first, last = chunks[run[0]], chunks[run[-1]]
        if last[0] + last[2] - first[0] >= MIN_STRETCH:
            out.append((first[0] + gap, last[0] + last[2] + gap, gap))
    return out


def clock(show: list[tuple[float, float, float]],
          copy: list[tuple[float, float, float]]) -> dict | None:
    """The copy's clock against the show's, the way the pairs files keep
    one: {"offset": n}, or {"sections": [...]} where it moves. Wherever a
    stretch of each went out at the same time, a second of the show is
    that second plus the difference of their two numbers on the copy; a
    stretch of the show the copy did not carry gets no clock at all."""
    sections = []
    for went, stopped, gap in stretches(show):
        for copy_went, copy_stopped, copy_gap in stretches(copy):
            lo, hi = max(went, copy_went), min(stopped, copy_stopped)
            if hi - lo >= MIN_STRETCH:
                sections.append({"from": int(round(lo - gap)), "to": int(round(hi - gap)),
                                 "offset": int(round(gap - copy_gap))})
    sections.sort(key=lambda s: s["from"])
    # Both replays skipping the same stretch splits each at the same place
    # and moves neither clock: one section, not two that agree.
    merged: list[dict] = []
    for section in sections:
        if merged and merged[-1]["to"] == section["from"] and merged[-1]["offset"] == section["offset"]:
            merged[-1]["to"] = section["to"]
        else:
            merged.append(section)
    sections = merged
    if sum(s["to"] - s["from"] for s in sections) < MIN_SHARED:
        return None
    if sections[-1]["to"] >= int(round(show[-1][0] + show[-1][2])) - 1:
        sections[-1]["to"] = None
    if len(sections) == 1 and sections[0]["from"] == 0 and sections[0]["to"] is None:
        return {"offset": sections[0]["offset"]}
    return {"sections": sections}


def loudness(media: str, start: float, seconds: float) -> np.ndarray | None:
    """Log loudness, a hundred readings a second, of `seconds` of the
    recording from `start`; None when X gives less than nine tenths of it."""
    try:
        run = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-rw_timeout", "30000000",
             "-ss", f"{max(0.0, start):.2f}", "-i", media, "-t", f"{seconds:.2f}",
             "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=120 + seconds)
    except subprocess.TimeoutExpired:
        return None
    sound = np.frombuffer(run.stdout, dtype=np.int16).astype(np.float64)
    n = len(sound) // HOP
    if n < seconds * (RATE / HOP) * 0.9:
        return None
    return np.log1p(np.sqrt((sound[:n * HOP].reshape(n, HOP) ** 2).mean(axis=1)))


def line_up(probe: np.ndarray, hay: np.ndarray) -> float | None:
    """Where in `hay` the stretch `probe` is, in seconds, or None unless it
    is there clearly: above SAME, and no other place within CLEAR of it."""
    m = len(probe)
    if len(hay) < m or probe.std() < 1e-6:
        return None
    p = (probe - probe.mean()) / probe.std()
    total = np.concatenate([[0.0], np.cumsum(hay)])
    squares = np.concatenate([[0.0], np.cumsum(hay ** 2)])
    mean = (total[m:] - total[:-m]) / m
    spread = np.sqrt(np.maximum((squares[m:] - squares[:-m]) / m - mean ** 2, 0.0))
    # p sums to nothing, so the window's own mean drops out of the product.
    r = np.correlate(hay, p, mode="valid") / m / np.where(spread > 1e-6, spread, np.inf)
    best = int(np.argmax(r))
    elsewhere = np.concatenate([r[:max(0, best - 100)], r[best + 101:]])
    if r[best] < SAME or (elsewhere.size and elsewhere.max() > r[best] - CLEAR):
        return None
    return best / (RATE / HOP)


def offset_at(show: str, copy: str, t: int, near: float) -> float | None:
    """The copy's second minus the show's at second `t` of the show, by
    the sound, looked for within REACH_SECONDS of `near`; None if not there."""
    probe = loudness(show, t, PROBE_SECONDS)
    if probe is None:
        return None
    start = max(0.0, t + near - REACH_SECONDS)
    hay = loudness(copy, start, t + near + REACH_SECONDS + PROBE_SECONDS - start)
    found = line_up(probe, hay) if hay is not None else None
    return None if found is None else start + found - t


def confirm(show: str, copy: str, found: dict, say=print) -> bool:
    """Whether the copy sounds like the show where the stamps put it: the
    middle of its longest stretches first, then their quarters."""
    sections = found.get("sections") or [{"from": 0, "to": None, "offset": found["offset"]}]
    last = max((s["to"] or s["from"] + 3600) for s in sections)
    places = []
    for share in (0.5, 0.25, 0.75):
        for s in sorted(sections, key=lambda s: (s["to"] or last) - s["from"], reverse=True):
            ends = s["to"] or last
            places.append((int(s["from"] + (ends - s["from"] - PROBE_SECONDS) * share), s["offset"]))
    for t, offset in places[:CHECKS]:
        heard = offset_at(show, copy, t, offset)
        say(f"     {t:>6}s  " + (f"{heard:+.2f}s by the sound, {offset:+d}s by the stamps"
                                 if heard is not None else "not heard"))
        if heard is not None:
            return abs(heard - offset) <= CHECK_AGREE
    return False


def measure(copy_post: str, show_post: str, say=print) -> dict | None:
    """{"broadcast", and the clock} for a copy of a show, or None."""
    copy, show = stream(copy_post), stream(show_post)
    if not (copy and show):
        say("     X would not give one of the two streams")
        return None
    show_chunks, copy_chunks = chunks(show[1]), chunks(copy[1])
    if not (show_chunks and copy_chunks):
        say("     a playlist without the times its chunks went out")
        return None
    found = clock(show_chunks, copy_chunks)
    if not found:
        say("     too little of the show went out on both")
        return None
    if not confirm(show[1], copy[1], found, say):
        say("     the sound did not line up where the stamps said")
        return None
    return {"broadcast": f"https://x.com/i/broadcasts/{copy[0]}", **found}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    print(json.dumps(measure(sys.argv[1], sys.argv[2]), indent=1))

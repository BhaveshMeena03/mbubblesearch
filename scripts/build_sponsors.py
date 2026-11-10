"""Every time Polymarket or Phantom was said on air on Market Bubble.

    .venv/bin/python scripts/build_sponsors.py

Reads the Market Bubble transcripts (data/episodes.json.gz, Market Bubble's
own broadcasts only, not Ansem's videos) and writes demo/sponsor-mentions.json,
which the page at /sponsors draws. Every line is quoted from the transcript
as it is, at the second it starts, with a link to that second on X where the
show is paired with its broadcast.

What counts is a sponsor's name, the way the transcripts spell it. The
transcripts are automatic and mishear names now and then, so this is every
mention that was caught: the true count can only be higher. Some lines that
match are not the sponsor at all, and those are left out by a rule that
says why (LEFT_OUT); the page lists them too, so nothing disappears
quietly. Each was found by reading every line on 10 Nov 2026.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "data" / "episodes.json.gz"
BROADCASTS = ROOT / "data" / "broadcast_links.json"
OUT = ROOT / "demo" / "sponsor-mentions.json"
EASTERN = ZoneInfo("America/New_York")

SPONSORS = {
    # The transcripts write it "Polymarket" and, as often, "poly market".
    "polymarket": ("Polymarket",
                   re.compile(r"\b(?:poly\s?-?\s?markets?|polly\s?markets?|pollymarkets?"
                              r"|polymarket'?s?)\b", re.I)),
    "phantom": ("Phantom", re.compile(r"\bphantoms?\b", re.I)),
}
# Lines that say the name and mean something else. The 4 Jun 2026 show has
# a guest's story about buying "a DJI Phantom One drone": three lines, no
# wallet in any of them.
LEFT_OUT = {
    "phantom": [(re.compile(r"\bDJI\b|\bdrone", re.I), "a DJI Phantom drone, not the wallet")],
}


def aired(episode_id: str) -> datetime:
    """When the post behind an X episode went up, from its id."""
    post = int(episode_id.removeprefix("x-"))
    return datetime.fromtimestamp(((post >> 22) + 1288834974657) / 1000, tz=UTC)


def link(episode_id: str, t: float, broadcasts: dict) -> tuple[str, bool]:
    """Where to watch a line: the broadcast at that second when the show
    is paired with one, otherwise the post itself (which X cannot open at
    a second, so the page shows the time beside it)."""
    if episode_id in broadcasts:
        return f"{broadcasts[episode_id]}?t={int(t)}", True
    return f"https://x.com/i/status/{episode_id.removeprefix('x-')}", False


def shows(rows: list[dict]) -> list[dict]:
    """Market Bubble's own broadcasts: X episodes, not Ansem's videos."""
    return [e for e in rows if str(e.get("episode_id", "")).startswith("x-")
            and not e.get("show")]


def build(rows: list[dict], broadcasts: dict) -> dict:
    episodes, left_out = [], []
    totals = {k: {"mentions": 0, "shows": 0} for k in SPONSORS}
    for ep in shows(rows):
        eid = ep["episode_id"]
        lines = []
        named = set()
        for seg in ep.get("segments") or []:
            text = seg["text"].strip()
            for key, (_, pattern) in SPONSORS.items():
                n = len(pattern.findall(text))
                if not n:
                    continue
                why = next((why for rule, why in LEFT_OUT.get(key, []) if rule.search(text)), None)
                url, seeks = link(eid, seg["t"], broadcasts)
                row = {"sponsor": key, "t": round(float(seg["t"]), 1), "text": text,
                       "count": n, "link": url, "seeks": seeks}
                if why:
                    left_out.append({**row, "episode_id": eid, "why": why})
                    continue
                lines.append(row)
                totals[key]["mentions"] += n
                named.add(key)
        for key in named:
            totals[key]["shows"] += 1
        if lines:
            when = aired(eid)
            episodes.append({"episode_id": eid, "title": ep.get("title"),
                             "aired": when.isoformat(timespec="seconds"),
                             "date": when.astimezone(EASTERN).date().isoformat(),
                             "lines": sorted(lines, key=lambda r: r["t"])})
    episodes.sort(key=lambda e: e["aired"], reverse=True)
    return {
        "sponsors": [{"key": k, "name": SPONSORS[k][0], **totals[k]} for k in SPONSORS],
        "shows_read": len(shows(rows)),
        # Which shows were read, so the list can be rebuilt from exactly
        # these after newer episodes have landed.
        "read": sorted(e["episode_id"] for e in shows(rows)),
        "episodes": episodes,
        "left_out": left_out,
    }


def main() -> None:
    rows = json.loads(gzip.decompress(EPISODES.read_bytes()))
    broadcasts = json.loads(BROADCASTS.read_text())
    got = build(rows, broadcasts)
    got["built_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    OUT.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
    for s in got["sponsors"]:
        print(f"{s['name']}: {s['mentions']} mentions in {s['shows']} of "
              f"{got['shows_read']} shows")
    print(f"left out: {len(got['left_out'])} lines; wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

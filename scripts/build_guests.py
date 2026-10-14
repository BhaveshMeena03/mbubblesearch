"""Build the guest pages from the quotes a person kept.

    .venv/bin/python scripts/build_guests.py

Goes in, all read by a person:

    data/guest_roster.json      who has been seen on camera, and the line
                                they were seen saying
    data/guest_voices.json      the lines in each one's voice
                                (scripts/guest_voices.py)
    data/guest_candidates.json  what scripts/extract_guest_quotes.py
                                proposed and app/guests.py did not refuse
    data/guest_verdicts.json    slug -> quote id -> {"keep": true,
                                "about": "a few words"}, and "lead": true
                                on the one a guest is introduced by

Comes out: data/guests.json, which is what the site serves.

A quote with no verdict does not ship. A kept quote that is no longer in
the transcript word for word, or no longer wholly in that guest's voice,
stops the build: that is what a re-transcribed show looks like, and a
page built past it would put words under the wrong name.

No network and no model. It can be run anywhere the repo is.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import calls, guests  # noqa: E402

DATA = ROOT / "data"
OUT = DATA / "guests.json"
HOME = ZoneInfo("America/Los_Angeles")
# A few seconds before the first word, so the link does not open on it.
RUN_UP = 6


def episodes() -> dict[str, dict]:
    plain = DATA / "episodes.json"
    rows = (json.loads(plain.read_text()) if plain.exists()
            else json.loads(gzip.decompress((DATA / "episodes.json.gz").read_bytes())))
    found: dict[str, dict] = {}
    for row in rows:
        found.setdefault(str(row.get("episode_id")), row)
    return found


def stamp(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def main() -> int:
    shows = episodes()
    roster = json.loads((DATA / "guest_roster.json").read_text())["guests"]
    voices = json.loads((DATA / "guest_voices.json").read_text())
    hosts = json.loads((DATA / "speaker_map.json").read_text())
    proposed = json.loads((DATA / "guest_candidates.json").read_text())
    verdicts = json.loads((DATA / "guest_verdicts.json").read_text())
    players = json.loads((DATA / "broadcast_links.json").read_text())

    problems, built = [], []
    for guest in roster:
        slug, eid = guest["slug"], guest["episode_id"]
        if not guests.is_slug(slug):
            problems.append(f"{slug!r} is not a usable address")
            continue
        episode, mine = shows.get(eid), voices.get(slug)
        if episode is None or not mine or mine["episode_id"] != eid:
            problems.append(f"{slug}: no show or no voice on file")
            continue
        player = players.get(eid)
        if not player:
            problems.append(f"{slug}: {eid} has no player that opens at a second")
            continue
        voice = set(mine["lines"])
        span = guests.on_air(voice, episode["segments"])
        when = calls.aired(eid, str(episode.get("published_at") or ""))
        by_id = {c["id"]: c for c in proposed.get(slug, {}).get("candidates", [])}
        quotes = []
        for ident, verdict in verdicts.get(slug, {}).items():
            if not verdict.get("keep"):
                continue
            if ident not in by_id:
                problems.append(f"{slug}: kept quote {ident} matches no candidate")
                continue
            quote, why = guests.accept(by_id[ident], episode, voice, hosts.get(eid, {}))
            if quote is None or quote["id"] != ident:
                problems.append(f"{slug}: {ident} no longer holds ({why or 'it moved'})")
                continue
            about = " ".join(str(verdict.get("about") or "").split())
            if not about:
                problems.append(f"{slug}: {ident} has no words saying what it is about")
                continue
            shown = {"id": ident, "quote": quote["quote"], "about": about,
                     "start_seconds": quote["start_seconds"],
                     "timestamp": stamp(quote["start_seconds"]),
                     "link": f"{player}?t={int(quote['start_seconds'])}"}
            if verdict.get("lead"):
                shown["lead"] = True
            quotes.append(shown)
        if not quotes:
            problems.append(f"{slug}: nothing kept, so there is no page to make")
            continue
        if sum(1 for q in quotes if q.get("lead")) > 1:
            problems.append(f"{slug}: more than one quote is marked as the lead")
            continue
        quotes.sort(key=lambda q: q["start_seconds"])
        built.append({
            "slug": slug, "name": guest["name"], "also": guest.get("also") or "",
            "role": guest.get("role") or "", "episode_id": eid,
            "show": " ".join(str(episode.get("title") or "").split()),
            "date": when.astimezone(HOME).date().isoformat(),
            "on_from": span[0], "on_to": span[1],
            "from_stamp": stamp(span[0]), "to_stamp": stamp(span[1]),
            "link": f"{player}?t={max(0, int(span[0]) - RUN_UP)}",
            "lines": len(voice), "quotes": quotes,
        })
    if problems:
        print("\n".join(f"  ! {p}" for p in problems))
        raise SystemExit(f"\n{len(problems)} problem(s). Nothing written.")
    built.sort(key=lambda g: (g["date"], g["on_from"]), reverse=True)
    OUT.write_text(json.dumps({
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "guests": built}, indent=1, ensure_ascii=False) + "\n")
    for g in built:
        print(f"  {g['date']} {g['name']:18} {len(g['quotes'])} quotes, on {g['from_stamp']} to {g['to_stamp']}  {g['role']}")
    print(f"\n{len(built)} guests, {sum(len(g['quotes']) for g in built)} quotes -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

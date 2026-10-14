"""Which lines are in each confirmed guest's voice.

    .venv/bin/python scripts/guest_voices.py

data/guest_roster.json names the guests a person has seen on camera and,
for each, the transcript line being spoken in the frame they were seen in.
label_speakers.py has already sorted every line of the show into voices
by fingerprint. So the voice of that one line is the guest's, and every
line in that voice while they were on is theirs too.

Only that one voice. A window can hold a second unnamed voice, and
write_guest_labels.py would have put the banner's name on it as well.

A line the hand-built host map says is Ansem's or Banks's is left out,
whatever the fingerprint says: a person vouched for that one by ear.

Needs data/speakers/, which is not in the repo, so this runs on the
machine that has the fingerprints. What it writes, data/guest_voices.json,
is line numbers and nothing else, and is tracked so the tests can use it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
ROSTER = DATA / "guest_roster.json"
OUT = DATA / "guest_voices.json"
# Fewer lines than this is not enough of somebody to make a page from.
ENOUGH = 60
# The guest's voice has to be the main unnamed voice in the window by
# this much, or the window holds two people and neither is claimed.
CLEAR_BY = 3.0
# How many lines the hand map has to name before a voice is a host's.
# The same number write_guest_labels.py uses.
HOST_EVIDENCE = 3


def main() -> int:
    episodes: dict[str, dict] = {}
    for episode in json.loads((DATA / "episodes.json").read_text()):
        episodes.setdefault(episode["episode_id"], episode)
    windows = json.loads((DATA / "guest_windows.json").read_text())
    hosts = json.loads((DATA / "speaker_map.json").read_text())
    out: dict[str, dict] = {}
    failed = []
    for guest in json.loads(ROSTER.read_text())["guests"]:
        slug, eid = guest["slug"], guest["episode_id"]
        prints = DATA / "speakers" / f"{eid}.json"
        if not prints.exists():
            failed.append(f"{slug}: no fingerprints for {eid}")
            continue
        voices = json.loads(prints.read_text())["segments"]
        segments = episodes[eid]["segments"]
        on = [w for w in windows.get(eid, []) if w["name"] == guest["plate"]]
        if not on:
            failed.append(f"{slug}: no window called {guest['plate']!r}")
            continue
        start, end = min(w["start"] for w in on), max(w["end"] for w in on)
        anchor_voice = voices.get(str(guest["anchor"]))
        if anchor_voice is None:
            failed.append(f"{slug}: line {guest['anchor']} has no fingerprint")
            continue
        inside = [i for i, s in enumerate(segments) if start <= s["t"] <= end]
        if guest["anchor"] not in inside:
            failed.append(f"{slug}: line {guest['anchor']} is outside the window")
            continue
        named = hosts.get(eid, {})
        # A voice the hand map names as a host three times or more,
        # anywhere in the show, is a host's voice wherever it turns up.
        # The map only labels the lines it is sure of, so a host has
        # plenty of lines here with no name on them.
        as_host: dict[int, int] = {}
        for index in named:
            voice = voices.get(index)
            if voice is not None:
                as_host[voice] = as_host.get(voice, 0) + 1
        host_voices = {v for v, n in as_host.items() if n >= HOST_EVIDENCE}
        if anchor_voice in host_voices:
            failed.append(f"{slug}: line {guest['anchor']} is in a host's voice")
            continue
        counts: dict[int, int] = {}
        for i in inside:
            voice = voices.get(str(i))
            if voice is not None and voice not in host_voices and str(i) not in named:
                counts[voice] = counts.get(voice, 0) + 1
        others = max((n for v, n in counts.items() if v != anchor_voice), default=0)
        mine = sorted(i for i in inside
                      if voices.get(str(i)) == anchor_voice and str(i) not in named)
        if len(mine) < ENOUGH:
            failed.append(f"{slug}: only {len(mine)} lines in that voice")
            continue
        if others and len(mine) < CLEAR_BY * others:
            failed.append(f"{slug}: a second voice has {others} lines against {len(mine)}")
            continue
        out[slug] = {"episode_id": eid, "lines": mine}
        print(f"  {slug:18} {len(mine):4} lines, next voice {others:3}, "
              f"from {int(segments[mine[0]]['t']) // 60}m to {int(segments[mine[-1]]['t']) // 60}m")
    if failed:
        print("\n".join(f"  ! {f}" for f in failed))
        raise SystemExit(f"\n{len(failed)} problem(s). Nothing written.")
    OUT.write_text(json.dumps(out, indent=None, separators=(",", ":")) + "\n")
    print(f"\n{len(out)} guests -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

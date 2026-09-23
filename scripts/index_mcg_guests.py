"""Who was on each MCG episode, from what the show says out loud.

    .venv/bin/python scripts/index_mcg_guests.py --episodes 3
    .venv/bin/python scripts/index_mcg_guests.py --all --out data/mcg_guests.json

Market Bubble knows who is speaking from voice fingerprints, and that works
because two hosts recur across every show: cluster the whole archive and
the two clusters present everywhere are Ansem and Banks. MCG has the
opposite shape -- one project per episode, 645 of them, and almost every
guest appears exactly once -- so the same method would name the hosts,
which nobody is asking about, and leave every founder unknown.

So this does not try. It answers the question one level up: who was on this
episode, and which project was it about. That is an episode-level fact the
show states itself, in the introductions, and it is what somebody actually
searches MCG for.

The rule this inherits from build_speaker_map.py, and the reason that file
leaves guests unknown: guessing from the title is how "Austin Federa said"
ended up attached to a quote from somebody at another company. So a guest
is recorded only when the transcript introduces them. The title is stored
as the project, which is a different and weaker claim, and it is never
turned into a speaker.

Nothing here is a speaker label. It never says a line belongs to a voice --
only that these people were in this episode, and where they were brought
on. Retrieval and the existing labels are untouched.

Cheap by construction: rather than reading 1,024.7 hours past the model, it
sends only the passages around an introduction cue, found by regex first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from anthropic import AsyncAnthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import anthropic_client_kwargs, get_settings  # noqa: E402
from scripts.extract_assets import USAGE, _meter  # noqa: E402
from scripts.extract_mcg_assets import rebuild  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mcg-guests")

SHELF = ROOT / "data" / "mcg_index.json"
OUT = ROOT / "data" / "mcg_guests.json"

# Somebody ARRIVING, or introducing themselves in their own voice. This is
# the test for presence, and it is separate from the cues below on purpose.
#
# The two were one list, and it let through exactly what this file is meant
# to stop. On the 20 Sep stream a guest was asked about his third
# co-founder and answered "of course he's on the website, his name is Micah
# Walter range" -- and the index recorded Micah Walter Range as a guest at
# 26:45. Every check passed: the name is real, it is spoken, it is at that
# second, and all three words of it are right there. He is simply not in
# the room. "co-founder" is said about an absent person as often as a
# present one, so it cannot be what decides presence.
#
# First person is the other half: a self-introduction is presence even with
# no arrival line ("i'm mike i'm the founder of route"). Third person is
# never presence, which is why "his name is" appears nowhere here.
PRESENCE = re.compile(
    r"\b(bring (?:him|her|them|up|on|in)|bringing (?:him|her|them|up|on)|"
    r"let'?s bring|welcome (?:to the|back|in|him|her|them)|joining us|"
    r"joined by|we (?:have|got) (?:with us|on)|on with us|"
    r"how (?:you|are you) doing|what'?s up (?:man|bro|brother)|"
    r"introduce yourself|tell (?:us|everyone) (?:a bit )?about yourself|"
    r"i'?m the (?:founder|co-?founder|ceo)|thanks for having (?:me|us))\b",
    re.I)


def introduces_self(name: str) -> re.Pattern:
    """"i'm Ferdy" -- presence, in his own voice, for THIS guest.

    Name-aware on purpose. A generic first-person pattern cannot tell
    "I'm Ferdy" from "I'm not sure", and a generic one that required a
    role ("i'm the founder") missed the commonest introduction on the
    show: the guest simply says his name. Tying it to the name being
    recorded makes it both wider and stricter -- it accepts "I'm Ferdy"
    and still has no way to accept "his name is Micah Walter range",
    because nobody says "I'm Micah" anywhere near it.
    """
    first = re.escape((name.split() or [""])[0])
    return re.compile(rf"\b(?:i'?m|i am|my name is|this is)\s+{first}\b", re.I)

# What is worth SENDING to the model. Wider than presence, because a role
# stated near an arrival is how the "founder of X" line gets attached to
# the person who just walked on.
CUES = re.compile(
    r"\b(bring (?:him|her|them|up|on|in)|bringing (?:him|her|them|up|on)|"
    r"let'?s bring|welcome (?:to the|back|in)|joining us|joined by|"
    r"we (?:have|got) (?:with us|on)|on with us|"
    r"founder of|co-?founder|the ceo of|our guy at|"
    r"introduce yourself|tell (?:us|everyone) (?:a bit )?about yourself|"
    r"who (?:are|do) you|what'?s up (?:man|bro|brother))\b", re.I)

# Seconds of transcript either side of a cue. An introduction runs longer
# than the line that triggers it: the name usually lands in the reply.
CONTEXT = 75

# Cap per episode. A four-hour "Market Update + Interviews" can fire the
# cue thirty times; the first several carry the introductions and the rest
# are the host being friendly.
MAX_PASSAGES = 8

TOOL = {
    "name": "record_guests",
    "description": "Record who was on this episode and what it was about.",
    "input_schema": {
        "type": "object",
        "properties": {
            "guests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "As the show says it. Empty if never said."},
                        "role": {"type": "string",
                                 "description": "Role and company, only if stated."},
                        "project": {"type": "string",
                                    "description": "The project they represent, if stated."},
                        "at_seconds": {"type": "number",
                                       "description": "Where they are introduced."},
                        "evidence": {"type": "string",
                                     "description": "The words that say so, quoted."},
                    },
                    "required": ["name", "at_seconds", "evidence"],
                },
            },
        },
        "required": ["guests"],
    },
}

SYSTEM = """\
You read excerpts from a crypto show and record WHO WAS IN THE ROOM.

Record a guest only when the excerpt introduces them or they introduce \
themselves. The name must be spoken in the text you are given.

Do NOT record:
- the hosts, or anyone only being talked ABOUT
- a person whose name you inferred rather than read
- a project mentioned in passing with nobody present for it

`evidence` must be a phrase copied from the excerpt. If nobody is \
introduced, return an empty list. An empty list is a correct answer and is \
much better than a guess."""


def passages(segments: list[dict]) -> list[str]:
    """The neighbourhoods of the archive where somebody gets introduced."""
    hits = [s["t"] for s in segments if CUES.search(s["text"])]
    if not hits:
        return []
    # Merge cues that sit inside one another's context, so a single
    # introduction does not become four overlapping excerpts.
    spans: list[list[float]] = []
    for at in hits:
        lo, hi = at - CONTEXT, at + CONTEXT
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = hi
        else:
            spans.append([lo, hi])

    out = []
    for lo, hi in spans[:MAX_PASSAGES]:
        lines = [f"[t={s['t']:.0f}] {s['text']}"
                 for s in segments if lo <= s["t"] <= hi]
        if lines:
            out.append("\n".join(lines))
    return out


async def read_episode(client, model: str, title: str,
                       excerpts: list[str]) -> list[dict]:
    body = "\n\n---\n\n".join(excerpts)
    resp = await client.messages.create(
        model=model, max_tokens=1500, system=SYSTEM, tools=[TOOL],
        # Extraction, not writing. At the default sampling temperature two
        # runs over the same four episodes disagreed: the first found
        # "Mike, founder of Route" and the second did not return him at
        # all. A pass over 645 episodes that cannot be reproduced is not
        # an index, it is a sample.
        temperature=0,
        tool_choice={"type": "tool", "name": "record_guests"},
        messages=[{"role": "user", "content":
                   f"Episode title: {title}\n\n<excerpts>\n{body}\n</excerpts>"}],
    )
    _meter(resp)
    for block in resp.content:
        if block.type == "tool_use":
            return block.input.get("guests", [])
    return []


def keep(guest: dict, segments: list[dict]) -> bool:
    """Drop anything the transcript does not support AT THE SECOND CLAIMED.

    The model is asked for quoted evidence, and the first version of this
    check only asked whether that quote existed somewhere in the episode.
    It does not survive contact: on a four-hour stream the model returned
    "Furrow" at 28:54 carrying the quote that introduces micah at 61:39,
    and the check passed it, because the words were indeed in the episode.
    A name on the wrong moment is the failure this whole file exists to
    avoid -- it is how "Austin Federa said" happened.

    So both the name and the quote have to appear in the transcript around
    the second being claimed, which is the same rule the reply auditor
    applies to anything this account publishes.
    """
    name = (guest.get("name") or "").strip()
    if not name or len(name) > 60:
        return False
    quote = (guest.get("evidence") or "").strip().lower()
    if not quote:
        return False

    try:
        at = float(guest.get("at_seconds"))
    except (TypeError, ValueError):
        return False

    near = " ".join(s["text"] for s in segments
                    if at - CONTEXT <= s["t"] <= at + CONTEXT).lower()
    if not near:
        return False
    if quote[:40] not in near:
        return False

    # Somebody has to arrive, or say their own name, within earshot of the
    # moment being claimed. Without this the index records people the show
    # merely described.
    if not (PRESENCE.search(near) or introduces_self(name).search(near)):
        return False

    # EVERY part of the name, not just the first. Checking the first token
    # alone accepted "Micah Walter Range" on the strength of "Micah", which
    # is how two people in one introduction become one person with a
    # surname that was never theirs. Short tokens are skipped because
    # initials and particles ("J.", "de") are not evidence either way.
    parts = [w for w in re.findall(r"[A-Za-z]+", name) if len(w) > 2]
    return bool(parts) and all(w.lower() in near for w in parts)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    if not args.all and not args.episodes:
        ap.error("pass --episodes N or --all")

    settings = get_settings()
    shelf = json.loads(SHELF.read_text())
    todo = shelf if args.all else shelf[:args.episodes]

    from pinecone import Pinecone
    index = Pinecone(api_key=settings.pinecone_api_key).Index(
        settings.mcg_pinecone_index)
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    model = os.environ.get("EXTRACT_MODEL", "claude-haiku-4-5")

    out_path = Path(args.out)
    known = json.loads(out_path.read_text()) if out_path.exists() else {}

    for n, row in enumerate(todo, 1):
        if row["id"] in known:
            continue
        segments = rebuild(index, settings.mcg_namespace,
                           settings.embedding_dimension, row["id"])
        excerpts = passages(segments)
        if not excerpts:
            known[row["id"]] = {"title": row["title"], "url": row["url"],
                                "guests": []}
            logger.info("[%d/%d] %-52s no introduction found",
                        n, len(todo), row["title"][:52])
            # Written here too. This branch used to `continue` straight
            # past the save at the bottom of the loop, which was invisible
            # until the last episode of a 645-episode run landed in it: the
            # file ended with 644 entries, and the missing one would have
            # been re-read on every future run as though it had never been
            # looked at.
            out_path.write_text(json.dumps(known, indent=1))
            continue
        found = await read_episode(client, model, row["title"], excerpts)
        kept = [g for g in found if keep(g, segments)]
        known[row["id"]] = {"title": row["title"], "url": row["url"],
                            "guests": kept}
        logger.info("[%d/%d] %-52s %d passage(s) -> %d guest(s)%s",
                    n, len(todo), row["title"][:52], len(excerpts), len(kept),
                    f" (dropped {len(found) - len(kept)} unsupported)"
                    if len(found) != len(kept) else "")
        for g in kept:
            logger.info("        %s — %s @ %s", g.get("name"),
                        g.get("role") or g.get("project") or "?",
                        f"{int(g.get('at_seconds', 0)) // 60}:"
                        f"{int(g.get('at_seconds', 0)) % 60:02d}")
        out_path.write_text(json.dumps(known, indent=1))

    named = sum(1 for v in known.values() if v["guests"])
    logger.info("\n  %d episodes read, %d with a named guest -> %s",
                len(known), named, out_path)
    if USAGE["calls"]:
        logger.info("  spent: %d calls, %s input, %s output tokens",
                    USAGE["calls"], f"{USAGE['input_tokens']:,}",
                    f"{USAGE['output_tokens']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

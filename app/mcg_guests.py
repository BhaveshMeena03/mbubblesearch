"""Who was on an MCG episode, and which project it was about.

Two claims of different strength, kept apart on purpose.

`guests` are people the show introduced out loud, extracted by
scripts/index_mcg_guests.py and verified against the transcript at the
second claimed. That file never guesses: 384 of 645 episodes have a name,
and the other 261 have an empty list rather than an inference.

`subject` is read from the title, and is NOT a speaker. MCG runs one
project per episode and names it in the title -- "Hookr: Hook Launchpad",
"$KARMA : $80K Volume In 1 Day" -- so what an episode is about is knowable
even where the founder's name was never said aloud. Turning that into "X
said" is the mistake build_speaker_map.py documents, where a title put
"Austin Federa said" on a quote from somebody at another company, so it
stays a separate field with a weaker name.

It is `subject` rather than `project` because that is what it honestly
is. Run over the real titles, most come back a project -- Dolphin, Scopl,
REPPO, ICM.Run -- but "Web3Adam: Making $500,000 from MILF Coin" is a
person, and the show is about him. Calling that a project would be a
category the data does not support, invented by this parser rather than
found in the title.

The parse is deliberately narrow. It answers on the shapes the channel
actually uses and returns None the moment a title stops looking like one
of them, because a title is free and a wrong project label is not.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "data" / "mcg_guests.json"

# A live market stream, not an episode about one project. These carry
# several interviews and name none of them in the title.
_LIVE = re.compile(r"^\s*(?:🔴|live\b|🔴\s*live\b)", re.I)

# What a project name is allowed to look like once isolated: one or two
# tokens, letters and digits, optionally a $ticker or a dotted name like
# $MILADY.AI. Anything wordier is a sentence, and a sentence is not a name.
_NAME = re.compile(r"^\$?[A-Za-z][A-Za-z0-9.&'-]*(?: [A-Za-z0-9.&'-]+)?$")

# Titles introduce the project in a handful of ways, and each one has a
# side of the phrase that carries the name:
#   "DevFunPump with RxbiXyz: ..."   -> before `with`
#   "Gavin on Paragon: ..."          -> after `on`
#   "Luca (Founder of JTVO) : ..."   -> inside the parenthesis, after `of`
_WITH = re.compile(r"\s+with\s+", re.I)
_ON = re.compile(r"\s+on\s+", re.I)

# The channel is never the subject. `on` points both ways -- "Gavin on
# Paragon" puts the project after it, "$PONS on MCG" puts the project
# before it -- and reading it one way only answered "MCG" for the second,
# which is the show every one of these episodes is on.
_CHANNEL = {"mcg", "mcg live", "mcglive", "the mcg show", "mcg show"}
_PAREN_OF = re.compile(r"\(([^)]*\bof\s+([^)]+))\)", re.I)


def subject_from_title(title: str) -> str | None:
    """What an episode is about, or None when the title does not say.

    Usually a project, sometimes the person the show is about. None is a
    perfectly good answer and the common one for live streams.
    """
    if not title or _LIVE.match(title):
        return None

    head = title.split("|")[0]

    # "Luca (Founder of JTVO)" names the subject inside the parenthesis,
    # and the person outside it. Read the parenthesis first, because the
    # text before it is a name and would otherwise win.
    paren = _PAREN_OF.search(head)
    if paren:
        return _clean(paren.group(2))

    head = head.split(":")[0] if ":" in head else head
    if _WITH.search(head):
        head = _WITH.split(head, 1)[0]
    elif _ON.search(head):
        before, after = _ON.split(head, 1)
        head = before if after.strip().lower() in _CHANNEL else after

    return _clean(head)


def _clean(candidate: str) -> str | None:
    name = candidate.strip().strip("-–—,").strip()
    if not name or len(name) > 40:
        return None
    # A title full of handles ("w/@fairscalexyz @stardotfun") is a panel
    # rather than a show about one thing.
    if "@" in name or "," in name:
        return None
    return name.lstrip("$") if _NAME.match(name) else None


@lru_cache(maxsize=1)
def load() -> dict:
    """episode_id -> {title, url, guests}. Empty when the file is absent.

    Absent is a normal state, not an error: the archive answers questions
    perfectly well without knowing who was in the room, and a missing file
    must never take the MCG routes down with it.
    """
    if not _PATH.exists():
        logger.info("no MCG guest index at %s; episodes will carry none", _PATH)
        return {}
    try:
        return json.loads(_PATH.read_text())
    except (OSError, ValueError) as exc:
        logger.warning("MCG guest index unreadable (%s); ignoring", exc)
        return {}


def for_episode(episode_id: str, title: str = "") -> dict:
    """What is known about one episode: who was on it, what it was about."""
    row = load().get(episode_id) or {}
    return {
        "guests": row.get("guests", []),
        "subject": subject_from_title(title or row.get("title", "")),
    }

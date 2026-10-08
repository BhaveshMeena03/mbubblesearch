"""The same second of an MCG Live show, on X.

MCG streams its daily show to YouTube and to X at once. The archive is
built from the YouTube copy, so every citation into it is a YouTube link,
and a YouTube link in a reply on X sends the reader to another site. The
X broadcast plays where the reader already is and opens at the second in
the link, the way Market Bubble's citations do.

It is one recording sent to two places, not two cuts of a show: measured
on the 7 October stream at 40 minutes and again at three and a half
hours, the same words sat 1.7 and 1.6 seconds earlier on X's clock than
on YouTube's, and all 484 shared eight-word runs agreed within five
seconds. So nothing is transcribed twice and nothing is indexed twice. A
show needs one fact, which broadcast it is, and one number, how far apart
the clocks are.

data/mcg_broadcast_links.json holds both, written by
scripts/pair_mcg_broadcasts.py, which measures the gap for each show
rather than assuming it. A show that is not in the file keeps its YouTube
link, which also opens at the second.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "data" / "mcg_broadcast_links.json"

_YOUTUBE = re.compile(
    r"(?:youtube\.com/watch\?(?:[^ ]*&)?v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_SECONDS = re.compile(r"[?&]t=(\d+)s?\b")


@lru_cache(maxsize=1)
def _pairs() -> dict[str, dict]:
    try:
        return json.loads(_PATH.read_text()) if _PATH.exists() else {}
    except Exception:  # noqa: BLE001
        # A reply that links YouTube is the reply this account has always
        # sent. Better that than no reply.
        logger.warning("could not read mcg_broadcast_links.json")
        return {}


def on_x(link: str) -> str:
    """This YouTube moment on the X broadcast of the same show, if the
    show went out on X. Anything else comes back as it was given."""
    video = _YOUTUBE.search(link or "")
    if not video:
        return link
    pair = _pairs().get(video.group(1))
    if not pair or not pair.get("broadcast"):
        return link
    at = _SECONDS.search(link)
    if not at:
        # No second to keep, so nothing to gain: the broadcast would open
        # at the waiting screen of a four hour stream.
        return link
    second = max(0, int(at.group(1)) + int(round(pair.get("offset", 0))))
    return f"{pair['broadcast']}?t={second}"


if __name__ == "__main__":
    # .venv/bin/python -m app.mcg_on_x "https://www.youtube.com/watch?v=<id>&t=2970s"
    # For a post written by hand: prints the X link, or the link it was
    # given when that show did not go out on X.
    import sys

    for given in sys.argv[1:]:
        print(on_x(given))

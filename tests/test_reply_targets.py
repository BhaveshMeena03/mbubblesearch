"""Which archive a reply under somebody's post is sourced from.

A receipt under a post has to come from the right show. Under Elon's post
it is Elon; under ThreadGuy's it is ThreadGuy on his own stream. Everyone
else gets whichever of the broadcast and ThreadGuy has the stronger line.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import find_reply_targets as frt  # noqa: E402


def test_elon_is_answered_from_the_musk_archive_only():
    assert frt.corpora_for("elonmusk") == ("elon",)
    assert frt.corpora_for("ElonMusk") == ("elon",)


def test_threadguy_and_his_network_are_answered_from_his_archive_only():
    assert frt.corpora_for("notthreadguy") == ("threadguy",)
    assert frt.corpora_for("CounterPartyTV") == ("threadguy",)


def test_everyone_else_is_searched_in_both_the_broadcast_and_threadguy():
    assert frt.corpora_for("HyperliquidX") == ("podcast", "threadguy")
    assert frt.corpora_for("blknoiz06") == ("podcast", "threadguy")


def test_both_threadguy_accounts_are_watched():
    watched = {h.lower() for h in frt.WATCHLIST}
    assert {"notthreadguy", "counterpartytv"} <= watched

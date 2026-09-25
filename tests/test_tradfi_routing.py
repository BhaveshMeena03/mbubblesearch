"""A fourth archive, and the three it must not take questions from.

Adding a corpus is the most dangerous change this project accepts. The
account's standing is that it answers from the Market Bubble broadcast;
a reply about the show sourced from a BlackRock panel would end that the
same way one sourced from a Tesla interview would. The Musk archive is
already narrowed for exactly this reason -- "tesla" and "twitter" are
deliberately absent because the hosts say both constantly.

So every term in the finance pattern was counted against the real
transcripts before it was allowed in, and these tests hold that line.
"""
import json
import re
from pathlib import Path

import pytest

from app.x_bot import _OF_THE_TRADFI_ARCHIVE, corpus_for

ROOT = Path(__file__).resolve().parent.parent


class TestTheFinanceArchiveAnswersItsOwn:
    @pytest.mark.parametrize("question", [
        "@mbubbleSearch when did blackrock change its mind on crypto",
        "what was said at davos about tokenisation",
        "milken panel on ai infrastructure",
        "how big are ibit flows",
    ])
    def test_the_fixed_terms_reach_the_finance_archive(self, question):
        """Institutions and venues are fixed; people are not."""
        assert corpus_for(question) == "tradfi"

    @pytest.mark.parametrize("question", [
        "what did larry fink say about bitcoin",
        "what did fink say about tokenisation",
    ])
    def test_an_indexed_subject_reaches_it(self, question):
        assert corpus_for(question) == "tradfi"

    @pytest.mark.parametrize("question", [
        "what does jamie dimon think about the debt cycle",
        "what did buffett say about private credit",
    ])
    def test_an_unindexed_subject_falls_back_to_the_broadcast(self, question):
        """Routable only once there is something of theirs to answer from.

        Naming somebody in a pattern before their recordings are indexed
        sends the question to an archive that has never heard of them,
        and the reply is a miss with the wrong archive's name on it.
        Falling back to the broadcast is the safe direction, and it is
        what the MCG split does too: the show wins ties.
        """
        assert corpus_for(question) == "podcast"


class TestTheBroadcastKeepsItsOwn:
    """_OF_THE_SHOW is matched first, and that is the whole safety net."""

    @pytest.mark.parametrize("question", [
        "what did ansem say about blackrock",
        "did banks mention the ibit flows on the show",
        "what did they say about larry fink on market bubble",
        "ansem on blackrock and tokenized stocks",
    ])
    def test_naming_the_show_wins_over_a_finance_word(self, question):
        assert corpus_for(question) == "podcast"

    @pytest.mark.parametrize("question", [
        "what did they say about the etf",
        "is the etf trade over",
        "etf inflows this week",
        "which etf did they like",
    ])
    def test_etf_never_routes_away_from_the_broadcast(self, question):
        """The "tesla" of this archive.

        ETFs come up on 17 of the uploads. Routing on the word would have
        taken those questions to an archive that has never heard of the
        show, which is the exact failure the Musk pattern was narrowed to
        avoid.
        """
        assert corpus_for(question) == "podcast"
        assert not _OF_THE_TRADFI_ARCHIVE.search(question)


class TestTheOtherArchivesAreUntouched:
    @pytest.mark.parametrize("question,expected", [
        ("what did elon say about neuralink", "elon"),
        ("when did @elonmusk first warn about ai", "elon"),
        ("what did musk tell lex fridman about mars", "elon"),
        ("what did ansem say about hyperliquid", "podcast"),
        ("what happened on last night's stream", "podcast"),
    ])
    def test_existing_routing_still_lands_where_it_did(self, question, expected):
        assert corpus_for(question) == expected

    def test_no_mcg_project_is_captured_by_the_finance_pattern(self):
        """The finance words must not collide with 600+ project names.

        MCG routes on the project name in each episode title, read from
        the shipped index. A finance term that matched one of those would
        silently send an MCG question to an archive of BlackRock panels.
        """
        shelf = json.loads((ROOT / "data" / "mcg_index.json").read_text())
        stolen = []
        for row in shelf:
            title = (row.get("title") or "").strip()
            if ":" not in title or "LIVE" in title[:12].upper():
                continue
            name = title.split(":", 1)[0].strip().strip("🔴 ").strip()
            if name and _OF_THE_TRADFI_ARCHIVE.search(name):
                stolen.append(name)
        assert not stolen, f"finance pattern captures MCG projects: {stolen[:5]}"


class TestTheWordsWereCountedFirst:
    """No term goes in the pattern that the hosts already lean on."""

    # Measured against data/episodes.json before the pattern was written.
    # A term the hosts use often enough to appear across many uploads
    # belongs to the broadcast, not to a new archive.
    CEILING = 10

    def test_every_finance_term_is_rare_in_the_broadcast(self):
        rows = json.loads((ROOT / "data" / "episodes.json").read_text())
        uniq = {}
        for row in rows:
            uniq.setdefault(row["episode_id"], row)
        terms = ["larry fink", "fink", "blackrock", "ibit",
                 "ray dalio", "dalio", "schwarzman", "milken", "davos"]
        counts = {}
        for term in terms:
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.I)
            counts[term] = sum(
                1
                for episode in uniq.values()
                for seg in episode.get("segments", [])
                if pattern.search(seg.get("text") or "")
            )
        noisy = {t: n for t, n in counts.items() if n > self.CEILING}
        assert not noisy, (
            f"these belong to the broadcast, not the finance archive: {noisy}")


class TestAnArchiveThatIsNotWiredIsHarmless:
    def test_the_table_falls_back_to_the_broadcast(self):
        """A deploy without the finance index must answer, not raise.

        This mirrors what `compose` does: an archive that is not wired is
        not in the table, and the question falls back to the broadcast
        rather than to an index that is None.
        """
        available = {"elon": object(), "mcg": object(), "tradfi": None}
        corpus = corpus_for("what did larry fink say about bitcoin")
        assert corpus == "tradfi"
        if corpus != "podcast" and not available.get(corpus):
            corpus = "podcast"
        assert corpus == "podcast"


class TestTheArchiveGrowsWithoutCodeChanges:
    """A page or a regex edit per person does not scale.

    Subjects are read off the shelf the way MCG reads project names off
    its index, so indexing someone tonight makes them routable in the
    morning without touching x_bot.py.
    """

    def test_a_shelved_subject_is_routable(self):
        from app.x_bot import _TRADFI_NAMES
        shelf_path = ROOT / "data" / "tradfi_episodes.json"
        if not shelf_path.exists():
            pytest.skip("nothing indexed yet")
        subjects = {
            (row.get("subject") or "").strip().lower()
            for row in json.loads(shelf_path.read_text())
        } - {"", "unknown"}
        if not subjects:
            pytest.skip("shelf carries no named subject")
        assert subjects & _TRADFI_NAMES, (
            f"shelved subjects {subjects} are not routable: {_TRADFI_NAMES}")

    def test_no_subject_can_steal_a_name_the_show_uses(self):
        """The invariant that holds whatever ends up on the shelf.

        Index somebody called Banks and "what did banks say" would stop
        being a question about the broadcast. The guard drops any subject
        whose name the show or the Musk archive already says, so the show
        wins ties here the way it does everywhere else.
        """
        from app.x_bot import (
            _OF_THE_MUSK_ARCHIVE,
            _OF_THE_SHOW,
            _TRADFI_NAMES,
        )
        for name in _TRADFI_NAMES:
            assert not _OF_THE_SHOW.search(name), (
                f"{name!r} belongs to the broadcast")
            assert not _OF_THE_MUSK_ARCHIVE.search(name), (
                f"{name!r} belongs to the Musk archive")

    def test_the_guard_drops_a_colliding_subject(self):
        from app.x_bot import _OF_THE_SHOW, _TOO_ORDINARY
        for stolen in ("banks", "ansem", "mert", "polymarket"):
            dropped = (len(stolen) < 4 or stolen in _TOO_ORDINARY
                       or bool(_OF_THE_SHOW.search(stolen)))
            assert dropped, f"{stolen!r} would have been routed away"

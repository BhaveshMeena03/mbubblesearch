"""A summary request must reach the stored summary, not retrieval.

Every episode has a summary written once from the whole transcript and
kept. A request that misses the trigger falls through to ordinary search
and gets answered from six passages instead -- a partial account of a
three-hour show, assembled from whatever ranked, while the real thing
sits ready.

Nine phrasings in fourteen used to fall through, "tldr" among them,
which is the common way to ask on X.

The trigger widened, but not by much. It has to be a word that only ever
means "summarise": "about" would have caught "what did they say about
hyperliquid in episode 17" and returned a summary where somebody wanted
an answer, which is a worse failure than the one being fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.x_bot import asks_for_the_latest, summary_request  # noqa: E402


class TestNumberedRequestsReachTheStoredSummary:
    @pytest.mark.parametrize("asked", [
        "summarize episode 17", "summarise ep 17", "summary of episode 17",
        "can you summarize episode 17", "recap ep 17", "rundown of episode 17",
        "what happened in episode 17",
        # The ones that used to fall through.
        "tldr episode 17", "tl;dr of episode 17", "give me a tldr of ep 17",
        "brief me on episode 17", "overview of episode 17",
        "break down episode 17", "breakdown of ep 17", "sum up episode 17",
    ])
    def test_it_resolves_the_number(self, asked):
        assert summary_request(asked) == 17, asked

    def test_a_two_digit_number_is_read_whole(self):
        assert summary_request("tldr episode 16") == 16
        assert summary_request("summarize ep 9") == 9


class TestTheLatestEpisode:
    @pytest.mark.parametrize("asked", [
        "summarize the latest episode", "summarise the newest episode",
        "tldr of the latest episode", "recap the most recent episode",
        "what happened in the last show",
        # 28 Oct 2026: these fell through to search and the model said it
        # could not find the newest show.
        "what was the latest market bubble episode about?",
        "whats in the most recent market bubble episode",
        "what's on the latest episode",
        "what did they talk about on the last episode",
        "what did they discuss in the latest market bubble episode",
        "latest episode?",
        "the newest show",
    ])
    def test_it_is_recognised_without_a_number(self, asked):
        """The model cannot know what is newest from six excerpts, so it
        must not be the one deciding."""
        assert asks_for_the_latest(asked), asked


class TestOrdinaryQuestionsAreNotHijacked:
    """The regression this widening could have caused. A summary returned
    where somebody asked a question is worse than the miss being fixed."""

    @pytest.mark.parametrize("asked", [
        "what did they say about hyperliquid in episode 17",
        "what did ansem say about solana in ep 16",
        "what did banks say about kick",
        "who was the guy who sold all his eth holding",
        "what did ansem say about the 17 million dollar wallet",
        "what did they say about the 2017 cycle",
        # A topic in the latest episode is still a question for search.
        "what did ansem say about sol in the latest episode",
        "what did they talk about bitcoin on the last episode",
        "what did banks say on the latest market bubble episode about kimchi",
        "what did they say about hyperliquid on the last episode",
        "is the latest episode bullish on zcash",
    ])
    def test_it_still_goes_to_search(self, asked):
        assert summary_request(asked) is None, asked
        assert not asks_for_the_latest(asked), asked


class TestTheStoredSummaryIsTheLongestCut:
    def test_an_unnumbered_broadcast_can_win(self):
        """A show exists as a live broadcast and a shorter YouTube cut.
        The broadcast runs about twice as long and its summary covers the
        third of the show the upload never carries -- so "episode 17"
        must reach the broadcast, not the cut."""
        from app.x_bot import _same_show_as, episode_number
        rows = [
            {"title": "Polymarket Fantasy Football Draft | Market Bubble #17",
             "published_at": "2026-08-29", "summary": "x" * 1296},
            {"title": "$100K POLYMARKET FANTASY FOOTBALL DRAFT NIGHT",
             "published_at": "2026-08-27", "summary": "y" * 3114},
        ]
        matches = [r for r in rows if episode_number(r["title"]) == 17]
        matches += _same_show_as(matches, rows)
        best = max(matches, key=lambda s: len(s["summary"]))
        assert len(best["summary"]) == 3114


class TestTheLatestShowIsTheFullBroadcast:
    """28 Oct 2026: the YouTube upload of episode 23 was the newest row by
    date, so "the latest episode" would have summarised a cut."""

    @staticmethod
    def rows(*specs):
        return [{"episode_id": eid, "published_at": day, "summary": "x", **extra}
                for eid, day, extra in specs]

    def test_the_upload_gives_way_to_its_broadcast(self):
        from app.x_bot import latest_show
        rows = self.rows(("x-23", "2026-10-22T20:30:00", {}),
                         ("81mlycpzKR4", "2026-10-23", {}),
                         ("x-token2049", "2026-10-19", {}))
        assert latest_show(rows)["episode_id"] == "x-23"

    def test_a_newer_broadcast_is_the_latest(self):
        from app.x_bot import latest_show
        rows = self.rows(("x-23", "2026-10-22", {}), ("x-24", "2026-10-29", {}),
                         ("81mlycpzKR4", "2026-10-23", {}))
        assert latest_show(rows)["episode_id"] == "x-24"

    def test_an_upload_with_no_broadcast_beside_it_stands(self):
        from app.x_bot import latest_show
        rows = self.rows(("x-23", "2026-10-15", {}), ("yt-only", "2026-10-23", {}))
        assert latest_show(rows)["episode_id"] == "yt-only"

    def test_an_appearance_on_another_show_is_not_the_latest_episode(self):
        from app.x_bot import latest_show
        rows = self.rows(("x-23", "2026-10-22", {}),
                         ("unchained", "2026-10-25", {"show": "Unchained"}))
        assert latest_show(rows)["episode_id"] == "x-23"

    def test_a_special_on_x_is_the_latest_when_it_is_newest(self):
        from app.x_bot import latest_show
        rows = self.rows(("x-23", "2026-10-22", {}), ("x-special", "2026-10-24", {}))
        assert latest_show(rows)["episode_id"] == "x-special"

    def test_nothing_dated_is_none(self):
        from app.x_bot import latest_show
        assert latest_show([{"episode_id": "a"}, {"episode_id": "b", "published_at": "soon"}]) is None

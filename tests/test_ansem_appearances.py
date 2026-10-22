"""Ansem's appearances on other shows, inside the Market Bubble archive.

22 Oct 2026: nineteen of his long interviews elsewhere (Unchained, Empire,
Lightspeed, UpOnly and more) went into this archive as a sub-category
rather than an archive of their own. Each episode carries `show`, the
name of the show it came from. These hold what that has to mean
everywhere: answers name the show, the page lists them apart, the counts
keep "Market Bubble episodes" true, and the Market Bubble token
dashboard never mines them.
"""

import asyncio
from pathlib import Path

from app.schemas import Episode

ROOT = Path(__file__).resolve().parent.parent


def appearance(**kw):
    base = dict(episode_id="HFuUgSvezlo",
                title="Unchained: Why Memecoins Can Be So Profitable",
                url="https://www.youtube.com/watch?v=HFuUgSvezlo",
                platform="youtube", published_at="2024-03-01",
                segments=[{"t": 0.0, "text": "welcome to unchained"},
                          {"t": 5.0, "text": "ansem thanks for coming on"}],
                show="Unchained")
    base.update(kw)
    return Episode(**base)


def test_a_market_bubble_episode_needs_no_show():
    ep = appearance(show=None)
    assert ep.show is None


def test_the_show_reaches_the_index_only_when_there_is_one():
    from app.podcast import PodcastIndex
    index = PodcastIndex.__new__(PodcastIndex)

    class S:
        chunk_max_chars = 10_000
    index._settings = S()
    rows = index.rows_for(appearance())
    assert rows and all(r["show"] == "Unchained" for r in rows)
    plain = index.rows_for(appearance(show=None))
    assert plain and all("show" not in r for r in plain)


def test_the_summary_carries_the_show():
    from app.summaries import SummaryStore
    store = SummaryStore.__new__(SummaryStore)
    written = []

    class Index:
        def upsert(self, vectors, namespace):
            written.extend(vectors)

    class S:
        embedding_dimension = 4
        pinecone_write_timeout_seconds = 5
    store._settings = S()
    store._index = Index()
    summary = "**TL;DR** " + "ansem on why memecoins pay. " * 12
    asyncio.run(store.store(appearance(), summary))
    assert written[0]["metadata"]["show"] == "Unchained"


def test_counts_keep_market_bubble_episodes_true(monkeypatch):
    from app import main
    rows = [{"episode_id": "a"}, {"episode_id": "b"},
            {"episode_id": "c", "show": "Unchained"}]
    monkeypatch.setattr(main, "_listed", lambda r: r)
    monkeypatch.setattr(main, "_episodes_by_id", lambda: {
        "a": {"segments": [{"t": 3600}]}, "b": {"segments": [{"t": 3600}]},
        "c": {"segments": [{"t": 3600}]}})

    class Summaries:
        async def list_all(self):
            return rows
    out = asyncio.run(main.podcast_archive(Summaries()))
    assert out == {"shows": 2, "appearances": 1, "hours": 3}


def test_answers_are_told_to_name_the_show():
    from app.podcast import SYSTEM_PROMPT
    assert "appearances on other people's shows" in SYSTEM_PROMPT
    assert "never \\\ncall it a Market Bubble episode" in SYSTEM_PROMPT or \
        "never call it a Market Bubble episode" in SYSTEM_PROMPT.replace("\n", " ")


def test_the_page_lists_them_apart():
    page = (ROOT / "demo" / "podcast.html").read_text()
    assert 'id="eps-guest-list"' in page and "Ansem on other shows" in page
    assert "ep.show && guest" in page and "a.appearances" in page


def test_the_market_bubble_token_dashboard_never_mines_them():
    assert 'if not e.get("show")' in (ROOT / "scripts" / "extract_assets.py").read_text()
    assert 'and not r.get("show")' in (ROOT / "scripts" / "sync_latest.py").read_text()


def test_the_bot_sends_questions_about_his_appearances_to_this_archive():
    """They live here, so this is where an Ansem question has to land."""
    from app.x_bot import corpus_for
    for q in ("@mbubbleSearch what did ansem say on unchained about memecoins",
              "@mbubbleSearch what did ansem say about friend.tech on empire",
              "@mbubbleSearch ansem on lightspeed about solana defi",
              "@mbubbleSearch what did ansem tell taiki maeda about eth",
              "@mbubbleSearch what did ansem say on uponly"):
        assert corpus_for(q) == "podcast", q

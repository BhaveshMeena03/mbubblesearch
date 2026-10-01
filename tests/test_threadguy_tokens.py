"""ThreadGuy's token dashboard, and the alias fix it exposed in MCG's.

The live MCG dashboard listed GTO beside JTVO, CLUTE beside CLUDE and DRIVE
beside DERIVE: the report served from the store was aggregated without the
archive's name, so that archive's own alias fixes never ran. Only the
committed fallback file had them. ThreadGuy's report goes through the same
builder, so the fix and its test come first.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main as main_module  # noqa: E402
from scripts import extract_mcg_assets as extractor  # noqa: E402

PAGE = (ROOT / "demo" / "threadguy-assets.html").read_text()
ARCHIVE_PAGE = (ROOT / "demo" / "threadguy.html").read_text()
NAV = (ROOT / "demo" / "nav.js").read_text()


class Store:
    def __init__(self, hits):
        self.hits = hits

    async def all_hits(self):
        return self.hits


def hit(symbol, name="", episode="e1"):
    return {"symbol": symbol, "name": name, "asset_class": "crypto",
            "kind": "analysis", "start_seconds": 10, "note": "n",
            "confidence": "high", "episode_id": episode,
            "episode_title": "t", "url": "https://www.youtube.com/watch?v=e1"}


def symbols(report):
    return {a["symbol"] for a in report["assets"]}


def test_the_live_report_applies_the_archives_own_aliases(tmp_path):
    hits = [hit("GTO"), hit("JTVO"), hit("CLUTE"), hit("CLUDE", episode="e2")]
    report = asyncio.run(main_module._build_report(
        Store(hits), tmp_path / "none.json", "_test_slot", "mcg"))
    assert symbols(report) == {"JTVO", "CLUDE"}
    delattr(main_module.app.state, "_test_slot")


def test_every_report_is_built_with_its_archive():
    src = (ROOT / "app" / "main.py").read_text()
    assert '"_assets_cache",\n                             "podcast")' in src
    assert '"_mcg_assets_cache", "mcg")' in src
    assert '"_threadguy_assets_cache", "threadguy")' in src
    assert "aggregate_assets(hits, archive=archive)" in src


def test_threadguy_assets_never_share_market_bubbles_namespace():
    """Both live in the default index. Sharing "assets" would put ThreadGuy
    moments under Market Bubble rows with links into the wrong show."""
    tg = extractor.ARCHIVES["threadguy"]
    assert tg["assets"] == "threadguy_assets"
    src = (ROOT / "app" / "main.py").read_text()
    assert 'AssetStore(index_name=_s.pinecone_index,\n' \
           '                                            namespace="threadguy_assets")' in src


def test_the_routes_and_shortcuts_exist():
    paths = {getattr(r, "path", "") for r in main_module.app.routes}
    assert {"/v1/threadguy/assets", "/v1/threadguy/assets/{symbol}",
            "/threadguy/tokens", "/threadguy/assets"} <= paths


def test_every_moment_is_dated_and_newest_first():
    """On a daily show a May take is not a September one."""
    assert 'fetch("/v1/threadguy/episodes")' in PAGE
    assert "DATES[e.episode_id] = e.published_at" in PAGE
    assert "if (dx !== dy) return dx < dy ? 1 : -1;" in PAGE


def test_this_week_counts_back_from_the_newest_stream_not_today():
    assert "CUTOFF = minusDays(NEWEST, 7)" in PAGE


def test_times_play_on_the_page():
    assert "https://www.youtube.com/embed/" in PAGE
    assert 'target="_blank" rel="noopener">\' +\n        esc(m.timestamp)' not in PAGE


def test_data_is_escaped_and_links_are_checked():
    assert "function esc(" in PAGE and "function safeUrl(" in PAGE
    assert "esc(m.note)" in PAGE and "esc(m.episode_title)" in PAGE
    assert "esc(a.name)" in PAGE


def test_no_dashes_in_what_a_visitor_reads():
    import re
    visible = re.sub(r"<!--.*?-->|/\*.*?\*/|//[^\n]*", "", PAGE, flags=re.S)
    assert "—" not in visible and "–" not in visible


def test_the_dashboard_can_be_found():
    assert '"/threadguy/tokens", name: "ThreadGuy tokens"' in NAV
    assert 'href="/threadguy/tokens">Tokens</a>' in ARCHIVE_PAGE

"""The Market Bubble page is laid out like the other archives' pages.

Until 20 November 2026 it was a centred column: a line of figures, a
headline, the search, then a list of fold-out summaries far below. It is
now the arrangement ThreadGuy's, rasmr's and MCG's pages use, a strip of
figures, the question beside the newest recordings, every episode by
month, with this page's own search, cards and share links kept as they
were. These hold both halves: the layout, and what was not to be lost.
"""

import asyncio
import re
from pathlib import Path

import pytest

from app import main, youtube_map

DEMO = Path(__file__).resolve().parent.parent / "demo"
PAGE = (DEMO / "podcast.html").read_text()
THREADGUY = (DEMO / "threadguy.html").read_text()
BODY = PAGE.split("<body>", 1)[1].split("<script>", 1)[0]
SCRIPT = PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
EPISODES = SCRIPT[SCRIPT.index("// ─── the episodes"):SCRIPT.index("// Auto-caption text is messy")]

# A live broadcast whose upload runs 100s behind it, with a cut after 600s.
LIVE = "x-2120000000000000900"
ANCHORS = {LIVE: [[t, "abcDEF12345", t - 100] for t in range(100, 600, 10)]}
BROADCAST = {"episode_id": LIVE, "title": "Market Bubble Episode 99",
             "url": "https://x.com/MarketBubble/status/900", "published_at": "2026-11-19",
             "summary": "# Episode 99\n\n**TL;DR:** They talk about **Zcash**.\n\n**Topics**\n"
                        "- [0:05:00] Ansem on Zcash.\n- [0:20:00] Cut from the upload.\n"
                        "- No time on this one.\n"}


def test_it_has_the_same_parts_in_the_same_order_as_the_other_rooms():
    parts = ['<div class="strip rise" id="strip"', '<section class="hero">',
             '<aside class="latest rise"', 'id="days"', 'id="stage"', 'id="answer"',
             'data-moments="podcast"', 'id="eps"', 'id="eps-guest"', "<footer"]
    found = [BODY.index(part) for part in parts]
    assert found == sorted(found)
    assert BODY.count("<div><span>") == 4            # the four figures
    for theirs in ('<div class="strip rise" id="strip"', '<section class="hero">',
                   '<aside class="latest rise"'):
        assert theirs in THREADGUY


def test_nothing_of_the_old_layout_is_left():
    for gone in ('class="eyebrow"', "details.epcard", 'class="chips"', "srcbadge",
                 "linkifyTimestamps", 'href="#eps" hidden', "ep.summary", "youtube_at"):
        assert gone not in PAGE, gone


@pytest.mark.parametrize("kept", [
    'data-act="x"', 'data-act="img"', 'data-act="clip"', "play on X", "play here",   # a card
    "function speakerChip(", "function momentCard(", "function postMoment(",
    'location.pathname+"?q="+encodeURIComponent(lastQuery)',                         # share link
    "history.replaceState(null", 'new URLSearchParams(location.search).get("q")',
    "hit.youtube_offset || 0", "function citeTimestamps(", "/v1/podcast/clip",
    'id="example-play"', "window.hideExample=function", 'id="ticks"',
])
def test_what_the_page_did_before_it_still_does(kept):
    assert kept in PAGE


def test_the_suggestions_are_the_checked_ones_and_still_search():
    tries = re.findall(r'<button type="button" class="try chip">([^<]+)</button>', BODY)
    assert tries == ["blackrock told an nba player not to buy bitcoin",
                     "how did he turn 500 dollars into 40 million",
                     "why does ansem think ethereum is done"]
    assert 'document.querySelectorAll(".chip").forEach' in SCRIPT


def test_the_figures_are_read_and_never_typed():
    for figure in ("s-eps", "s-guest", "s-hours", "s-last"):
        assert f'<b id="{figure}">&middot;</b>' in BODY, figure
        assert f'figure("{figure}"' in EPISODES, figure
    assert not re.search(r"\d+ (episodes|hours)", re.sub(r"<[^>]+>", " ", BODY))


# --- what the server sends for each episode -------------------------------------

def test_a_broadcast_says_where_each_time_plays(monkeypatch):
    monkeypatch.setattr(youtube_map, "_anchors", lambda: ANCHORS)
    sent = main._episode_row(BROADCAST, {LIVE: "https://x.com/i/broadcasts/1abcDEFghij"},
                             {LIVE: {"segments": [{"t": 10}, {"t": 5400.6}]}})
    assert sent["youtube"] == "abcDEF12345" and sent["seconds"] == 5400
    assert sent["own"] is False and sent["show"] == ""
    assert sent["tldr"] == "They talk about **Zcash**."
    assert "summary" not in sent
    heading, on_youtube, cut, untimed = sent["blocks"]
    assert heading == {"kind": "h", "text": "Topics"}
    # On YouTube at its own second, and on X at the broadcast's.
    assert on_youtube["yt"] == ["abcDEF12345", 200] and on_youtube["t"] == 300
    assert on_youtube["link"] == "https://x.com/i/broadcasts/1abcDEFghij?t=300"
    # Past the upload's last anchor: X only, at the second.
    assert "yt" not in cut and cut["link"] == "https://x.com/i/broadcasts/1abcDEFghij?t=1200"
    assert untimed == {"kind": "li", "text": "No time on this one."}


def test_a_broadcast_nobody_paired_has_times_as_text(monkeypatch):
    """A status link does not open at a second, so it is never offered."""
    monkeypatch.setattr(youtube_map, "_anchors", lambda: {})
    sent = main._episode_row(BROADCAST, {}, {})
    assert sent["youtube"] == "" and sent["seconds"] == 0
    assert all("link" not in b and "yt" not in b for b in sent["blocks"])
    assert sent["url"] == "https://x.com/MarketBubble/status/900"


def test_an_interview_from_youtube_plays_as_itself():
    row = {"episode_id": "PTWv24czW78", "title": "Ansem on a show", "show": "Unchained",
           "url": "https://www.youtube.com/watch?v=PTWv24czW78", "published_at": "2025-03-01",
           "summary": "**TL;DR:** An interview.\n\n- [0:10:00] Memecoins."}
    sent = main._episode_row(row, {}, {})
    assert sent["youtube"] == "PTWv24czW78" and sent["show"] == "Unchained"
    assert sent["blocks"][0]["yt"] == ["PTWv24czW78", 600]


def test_a_link_that_is_not_a_recording_is_not_sent():
    row = dict(BROADCAST, url="javascript:alert(1)")
    assert main._episode_row(row, {}, {})["url"] == ""


def test_one_row_that_cannot_be_read_costs_one_row(monkeypatch):
    rows = [dict(BROADCAST, episode_id=f"{LIVE[:-1]}{n}") for n in range(3)]
    monkeypatch.setattr(main, "_listed", lambda r: r)
    monkeypatch.setattr(main, "_episodes_by_id", lambda: {})
    monkeypatch.setattr(main, "_json_object", lambda path: {})
    real = main._episode_row

    def flaky(row, players, lengths):
        if row["episode_id"].endswith("901"):
            raise ValueError("a row nobody can read")
        return real(row, players, lengths)
    monkeypatch.setattr(main, "_episode_row", flaky)

    class Summaries:
        async def list_all(self):
            return rows
    sent = asyncio.run(main.podcast_episodes(Summaries()))
    assert [r["episode_id"][-3:] for r in sent] == ["900", "902"]
    assert all(r["tldr"] for r in sent)


def test_the_list_that_ships_can_all_be_drawn():
    """Every shipped row, through the same function the page is fed by."""
    from app.summaries import load_shipped
    rows = main._listed(list(load_shipped().values()))
    players = main._json_object(main.x_posts.BROADCAST_LINKS)
    sent = [main._episode_row(row, players, main._episodes_by_id()) for row in rows]
    assert len(sent) == len(rows) >= 40
    shows = [r for r in sent if not r["show"]]
    assert len(shows) >= 20 and all(r["tldr"] and r["blocks"] for r in shows)
    # Most of the show has an upload, so most of it plays in the page.
    assert sum(1 for r in shows if r["youtube"]) >= len(shows) * 0.8
    for r in sent:
        assert not (r["own"] and r["youtube"]), r["episode_id"]
        for b in r["blocks"]:
            assert b["kind"] in ("h", "li", "p")
            if "link" in b:
                # The broadcast's own player on X, or the YouTube video
                # an interview was indexed from. Never a status link,
                # which opens at the start whatever second it is given.
                assert re.fullmatch(r"https://x\.com/i/broadcasts/\w+\?t=\d+"
                                    r"|https://www\.youtube\.com/watch\?v=[\w-]{11}&t=\d+s",
                                    b["link"]), b


# --- how the page draws it ------------------------------------------------------

def test_a_summary_is_built_from_text_and_never_as_markup():
    """The summaries are model output. The only markup written in the
    episode list is the tile's own empty frame and two fixed messages."""
    written = re.findall(r"innerHTML\s*=\s*([^;]+);", EPISODES)
    assert written, "the episode list should still be found"
    for value in written:
        value = value.strip()
        assert value in ('""', "msg") or value.startswith("'<div class="), value
        assert not re.search(r"\b(ep|b|list|text)\.", value), value
    assert "rich(lead,ep.tldr)" in EPISODES and "document.createTextNode(part)" in EPISODES


def test_only_a_real_video_id_or_a_known_site_is_ever_opened():
    assert "var VIDEO=/^[A-Za-z0-9_-]{11}$/;" in EPISODES
    assert "if(!slot||!VIDEO.test(video||\"\")) return;" in EPISODES
    assert r"/^https:\/\/(x\.com|www\.youtube\.com)\//.test(b.link)" in EPISODES
    assert "out.href=safeUrl(ep.url);" in EPISODES
    assert "www.youtube.com/embed/" not in PAGE


def test_opened_from_a_link_a_summary_does_not_start_playing():
    """Nobody pressed play. A row that is pressed does."""
    assert "toggleRow(card,ep,false)" in EPISODES and "toggleRow(el,ep,true)" in EPISODES
    assert '+(auto?"&autoplay=1":"")' in EPISODES


def test_a_key_pressed_inside_an_open_summary_is_not_a_press_on_its_row():
    press = EPISODES[EPISODES.index("function pressable("):][:600]
    assert "if(e.target!==el) return;" in press
    assert 'box.addEventListener("click",function(ev){ ev.stopPropagation(); });' in EPISODES


def test_a_new_question_closes_the_episode_above_the_answer():
    asked = SCRIPT[SCRIPT.index("function search(){"):][:1600]
    assert "shut(); quiet();" in asked


def test_every_way_into_the_page_that_was_posted_still_lands():
    """#ansem and #ep-<id> are in posts, in the bot's replies and in the
    X extension's panel (app/x_posts.py)."""
    hashes = EPISODES[EPISODES.index("function goToHash(){"):][:900]
    for want in ('want === "ansem"', 'want === "eps-guest"', 'want==="eps"',
                 'want.indexOf("ep-") === 0'):
        assert want in hashes, want
    assert 'id="eps"' in BODY and 'id="eps-guest"' in BODY
    from app import x_posts
    posted = Path(x_posts.__file__).read_text()
    assert '"podcast": "/demo/podcast.html"' in posted and "#ep-{episode_id}" in posted
    assert 'el.id="ep-"+ep.episode_id;' in EPISODES


def test_the_page_has_no_dashes_in_its_own_words():
    said = re.sub(r"<!--.*?-->", "", BODY, flags=re.S)
    assert "—" not in said and "–" not in said
    for line in SCRIPT.splitlines():
        code = line.split("//", 1)[0]
        if "—" in code:
            assert code.lstrip().startswith(("*", "/*")) or "actually want to click" in line, line

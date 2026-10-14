"""The guest pages: nobody is quoted under a name that is not theirs.

Two halves. The first holds the rules in app/guests.py against small
made-up shows. The second holds every quote that is actually shipped
(data/guests.json) against the transcript, the lines in that guest's
voice, the hosts' labels and the list of who has been seen on camera, so
a page cannot drift from any of them without a test going red.
"""

import gzip
import io
import json
import re
from pathlib import Path

import pytest

from app import calls, guests, og_card

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGE = (ROOT / "demo" / "guest.html").read_text()
LIST = (ROOT / "demo" / "guests.html").read_text()


def show(*lines, eid="x-1"):
    return {"episode_id": eid,
            "segments": [{"t": float(i * 10), "text": text} for i, text in enumerate(lines)]}


LONG = "the entire premise of this stuff is that it avoids government regulation"


# --- an address ------------------------------------------------------------------

@pytest.mark.parametrize("value", ["mayne", "erik-voorhees", "a1", "brett-harrison-2"])
def test_a_plain_name_is_an_address(value):
    assert guests.is_slug(value)


@pytest.mark.parametrize("value", [
    "", "Erik", "erik voorhees", "erik_voorhees", "-erik", "erik-", "erik--v",
    "../etc/passwd", "erik/voorhees", "erik.png", "erik%2f", "<script>", "é",
    "a" * 41, None, 7, ["erik"], "erik\n", "erik\x00"])
def test_anything_else_is_not(value):
    assert not guests.is_slug(value)


# --- whose voice -----------------------------------------------------------------

def test_a_run_that_opens_and_closes_in_the_guests_voice_is_theirs():
    assert guests.in_voice({4, 5, 6}, {}, 4, 6)


def test_a_run_that_opens_in_another_voice_is_not():
    assert not guests.in_voice({5, 6}, {}, 4, 6)


def test_a_run_that_closes_in_another_voice_is_not():
    assert not guests.in_voice({4, 5}, {}, 4, 6)


def test_one_host_line_inside_refuses_the_whole_run():
    assert not guests.in_voice({4, 5, 6}, {"5": "Ansem"}, 4, 6)


def test_a_host_label_refuses_even_when_the_fingerprint_says_guest():
    """The hand label wins: a fingerprint is a guess and a label is not."""
    assert not guests.in_voice({4}, {"4": "Banks"}, 4, 4)


def test_a_short_line_with_no_voice_may_sit_inside_a_sentence():
    assert guests.in_voice({4, 6, 7}, {}, 4, 7)          # 1 unknown of 4


def test_more_than_a_third_unknown_is_not_enough_of_a_voice():
    assert not guests.in_voice({4, 7}, {}, 4, 7)         # 2 unknown of 4


# --- a proposed quote ------------------------------------------------------------

def test_a_quote_said_word_for_word_in_that_voice_is_kept():
    episode = show("welcome to the show", LONG, "right")
    quote, why = guests.accept({"line": 1, "quote": LONG, "about": "what crypto is for"},
                               episode, {1}, {})
    assert why == ""
    assert quote == {"id": "x-1:1", "line": 1, "last_line": 1, "start_seconds": 10.0,
                     "quote": LONG, "about": "what crypto is for"}


def test_the_words_shown_are_the_transcripts_not_the_models():
    """A model tidies. What ships is cut from the transcript itself."""
    episode = show("The entire premise of this stuff, is that it avoids Government regulation!")
    quote, _ = guests.accept({"line": 0, "quote": LONG}, episode, {0}, {})
    assert quote["quote"].startswith("The entire premise of this stuff, is that")


def test_a_quote_nobody_said_is_refused():
    episode = show(LONG)
    quote, why = guests.accept(
        {"line": 0, "quote": "the entire premise of this stuff is that it invites government regulation"},
        episode, {0}, {})
    assert quote is None and why == "quote is not in the transcript"


def test_a_quote_in_somebody_elses_voice_is_refused():
    quote, why = guests.accept({"line": 0, "quote": LONG}, show(LONG), {3}, {})
    assert quote is None and why == "not in the guest's voice"


def test_a_quote_a_host_said_is_refused_whatever_the_voice_list_says():
    quote, why = guests.accept({"line": 0, "quote": LONG}, show(LONG), {0}, {"0": "Ansem"})
    assert quote is None and why == "not in the guest's voice"


@pytest.mark.parametrize("said", ["yeah that is right", "word " * 61])
def test_too_short_or_too_long_is_refused(said):
    quote, why = guests.accept({"line": 0, "quote": said}, show(said), {0}, {})
    assert quote is None and why == "too short or too long"


@pytest.mark.parametrize("line", [None, "", "soon", [1], {}])
def test_a_quote_with_no_line_is_refused(line):
    quote, why = guests.accept({"line": line, "quote": LONG}, show(LONG), {0}, {})
    assert quote is None and why == "no line"


def test_a_show_with_no_transcript_refuses_everything():
    quote, why = guests.accept({"line": 0, "quote": LONG}, {"episode_id": "x-1"}, {0}, {})
    assert quote is None and why == "quote is not in the transcript"


def test_a_label_is_one_line_and_not_long():
    quote, _ = guests.accept({"line": 0, "quote": LONG, "about": "  what\n crypto   is for " + "x" * 200},
                             show(LONG), {0}, {})
    assert quote["about"].startswith("what crypto is for") and len(quote["about"]) == 80


def test_two_quotes_from_one_moment_count_once():
    found = [{"line": 10, "last_line": 12}, {"line": 14, "last_line": 15},
             {"line": 30, "last_line": 30}]
    assert [q["line"] for q in guests.apart(found)] == [10, 30]


def test_on_air_runs_from_the_first_line_to_the_last():
    episode = show(*["x"] * 9)
    assert guests.on_air({3, 5, 7}, episode["segments"]) == (30.0, 70.0)
    assert guests.on_air({99}, episode["segments"]) is None
    assert guests.on_air(set(), episode["segments"]) is None


# --- as served -------------------------------------------------------------------

def guest(**changes):
    row = {"slug": "erik-voorhees", "name": "Erik Voorhees", "also": "", "role": "",
           "episode_id": "x-1", "show": "LIVE W/ ERIK", "date": "2026-06-04",
           "on_from": 3878.0, "on_to": 6616.0, "from_stamp": "1:04:38",
           "to_stamp": "1:50:16", "link": "https://x.com/i/broadcasts/1dxYllbQZELJX?t=3872",
           "lines": 167,
           "quotes": [{"id": "x-1:10", "quote": "first thing said out loud", "about": "one",
                       "start_seconds": 4000.0, "timestamp": "1:06:40",
                       "link": "https://x.com/i/broadcasts/1dxYllbQZELJX?t=4000"},
                      {"id": "x-1:20", "quote": LONG, "about": "two", "lead": True,
                       "start_seconds": 4100.0, "timestamp": "1:08:20",
                       "link": "https://x.com/i/broadcasts/1dxYllbQZELJX?t=4100"}]}
    row.update(changes)
    return row


def test_a_guest_is_introduced_by_the_marked_quote():
    assert guests.lead(guest())["id"] == "x-1:20"


def test_or_by_the_first_one_said_when_none_is_marked():
    row = guest()
    del row["quotes"][1]["lead"]
    assert guests.lead(row)["id"] == "x-1:10"
    assert guests.lead({"quotes": []}) == {}


def test_the_list_is_newest_first_and_carries_the_lead_line():
    shipped = {"built_at": "t", "guests": [guest(), guest(slug="mayne", name="Mayne", date="2026-08-01")]}
    listed = guests.index(shipped)
    assert [g["slug"] for g in listed["guests"]] == ["mayne", "erik-voorhees"]
    assert listed["guests"][1] == {"slug": "erik-voorhees", "name": "Erik Voorhees", "role": "",
                                   "show": "LIVE W/ ERIK", "date": "2026-06-04",
                                   "quotes": 2, "first": LONG}
    assert guests.index({}) == {"guests": [], "built_at": None}


def test_two_guests_on_one_show_are_listed_later_one_first():
    shipped = {"guests": [guest(slug="a", on_from=100.0), guest(slug="c", on_from=900.0),
                          guest(slug="b", on_from=500.0)]}
    assert [g["slug"] for g in guests.index(shipped)["guests"]] == ["c", "b", "a"]


def test_one_guest_is_found_by_address_and_nothing_else():
    shipped = {"guests": [guest()]}
    assert guests.page(shipped, "erik-voorhees")["name"] == "Erik Voorhees"
    for wrong in ("mayne", "Erik-Voorhees", "erik-voorhees/", "../erik-voorhees", "", None):
        assert guests.page(shipped, wrong) is None
    assert guests.page({}, "erik-voorhees") is None


# --- as written ------------------------------------------------------------------

def test_a_date_is_written_out_in_full():
    assert guests.long_date("2026-08-13") == "August 13, 2026"
    assert guests.long_date("2026-06-04T20:00:00+00:00") == "June 4, 2026"
    for wrong in ("", None, "soon", "2026-13-40", 20260813):
        assert guests.long_date(wrong) == ""


@pytest.mark.parametrize("link", [
    "https://x.com/i/broadcasts/1dxYllbQZELJX?t=3872",
    "https://x.com/i/broadcasts/1kJzDPPQlqyKv?t=0"])
def test_the_shows_own_player_at_a_second_is_a_link(link):
    assert guests.player(link) == link


@pytest.mark.parametrize("link", [
    "", None, "javascript:alert(1)", "http://x.com/i/broadcasts/1dxYllbQZELJX?t=1",
    "https://x.com/i/broadcasts/1dxYllbQZELJX", "https://x.com/someone/status/123?t=4",
    "https://x.com.evil.example/i/broadcasts/1dxYllbQZELJX?t=1",
    "https://evil.example/?https://x.com/i/broadcasts/1dxYllbQZELJX?t=1",
    "https://x.com/i/broadcasts/1dxYllbQZELJX?t=1&next=//evil.example",
    "https://x.com/i/broadcasts/1dxYllbQZELJX?t=1\" onclick=\"x",
    "https://x.com/i/broadcasts/1dxYllbQZELJX?t=1\n", " https://x.com/i/broadcasts/1dxYllbQZELJX?t=1"])
def test_anything_else_is_not_somewhere_a_visitor_is_sent(link):
    assert guests.player(link) == ""


def test_a_page_is_filled_in_one_pass():
    """A value that looks like a field is text, not a field."""
    assert guests.fill("<b>{{name}}</b> {{role}}", {"name": "{{role}}", "role": "host"}) \
        == "<b>{{role}}</b> host"


def test_a_field_nobody_supplied_is_an_error_not_a_pair_of_braces():
    with pytest.raises(KeyError):
        guests.fill("{{name}} {{nobody}}", {"name": "x"})


def test_the_page_names_the_guest_everywhere_a_crawler_looks():
    out = guests.render_page(PAGE, guest(role="Founder of Venice"))
    assert "<title>Erik Voorhees on Market Bubble · quoted to the second</title>" in out
    assert '<meta property="og:title" content="Erik Voorhees on Market Bubble">' in out
    assert '<meta name="twitter:title" content="Erik Voorhees on Market Bubble">' in out
    assert ('<meta name="description" content="Erik Voorhees, Founder of Venice, on Market '
            'Bubble on June 4, 2026. Quoted word for word, each line linked to the second '
            'it was said.">') in out
    assert f'content="“{LONG}”"' in out
    assert '<meta property="og:url" content="https://search.lexthedev.com/guests/erik-voorhees">' in out
    assert '<link rel="canonical" href="https://search.lexthedev.com/guests/erik-voorhees">' in out
    card = "https://search.lexthedev.com/og/guest/erik-voorhees.png"
    assert f'<meta property="og:image" content="{card}">' in out
    assert f'<meta name="twitter:image" content="{card}">' in out


def test_the_page_shows_every_quote_with_its_subject_and_its_second():
    out = guests.render_page(PAGE, guest())
    assert "<h1>Erik Voorhees</h1>" in out
    assert "On air from <b>1:04:38</b> to <b>1:50:16</b>" in out
    assert out.count('<article class="said">') == 2
    assert "<blockquote>first thing said out loud</blockquote>" in out
    assert f"<blockquote>{LONG}</blockquote>" in out
    assert ('<a class="watch" href="https://x.com/i/broadcasts/1dxYllbQZELJX?t=4100" '
            'target="_blank" rel="noopener">Watch at 1:08:20</a>') in out
    assert ('href="https://x.com/i/broadcasts/1dxYllbQZELJX?t=3872" target="_blank" '
            'rel="noopener">Watch from 1:04:38</a>') in out


def test_no_field_is_left_unfilled():
    for row in (guest(), guest(role="CEO", also="TraderErik"), guest(quotes=[], link="")):
        assert "{{" not in guests.render_page(PAGE, row)
    assert "{{" not in guests.render_index(LIST, {"guests": [guest()]})


def test_a_guest_with_no_banner_title_has_no_empty_line_for_one():
    assert 'class="role"' not in guests.render_page(PAGE, guest()).split("</style>")[1]
    assert '<p class="role">CEO of Jito Labs</p>' in guests.render_page(
        PAGE, guest(role="CEO of Jito Labs"))


def test_another_name_is_shown_when_there_is_one():
    assert '<span class="also">also TraderMayne</span>' in guests.render_page(
        PAGE, guest(also="TraderMayne"))
    assert 'class="also"' not in guests.render_page(PAGE, guest()).split("</style>")[1]


HOSTILE = '"><script>alert(1)</script><img src=x onerror=\'y\'>'


@pytest.mark.parametrize("field", ["name", "role", "also", "from_stamp", "to_stamp"])
def test_nothing_in_a_guests_details_can_become_markup(field):
    out = guests.render_page(PAGE, guest(**{field: HOSTILE}))
    assert "<script>alert" not in out and "<img src=x" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


@pytest.mark.parametrize("field", ["quote", "about", "timestamp"])
def test_nothing_in_a_quote_can_become_markup(field):
    row = guest()
    row["quotes"][1][field] = HOSTILE
    out = guests.render_page(PAGE, row)
    assert "<script>alert" not in out and "<img src=x" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_a_quote_whose_link_is_not_the_player_is_shown_without_one():
    row = guest(link="javascript:alert(1)")
    row["quotes"][0]["link"] = "https://evil.example/?t=1"
    out = guests.render_page(PAGE, row)
    assert "evil.example" not in out and "javascript:" not in out
    assert '<span class="at">1:06:40</span>' in out
    assert "Watch from" not in out


def test_the_list_links_each_guest_to_their_own_page():
    shipped = {"guests": [guest(), guest(slug="mayne", name="Mayne", role="Trader",
                                         date="2026-08-01")]}
    out = guests.render_index(LIST, shipped)
    assert out.index('href="/guests/mayne"') < out.index('href="/guests/erik-voorhees"')
    assert '<b>Mayne</b><span class="role">Trader</span>' in out
    assert f'<span class="line">{LONG}</span>' in out
    assert '<span class="day">June 4, 2026</span>' in out


def test_the_list_cannot_be_given_markup_or_a_strange_address():
    shipped = {"guests": [guest(name=HOSTILE), guest(slug="../admin", name="Nobody")]}
    out = guests.render_index(LIST, shipped)
    assert "<script>alert" not in out
    assert "../admin" not in out and "Nobody" not in out


@pytest.mark.parametrize("template", [PAGE, LIST])
def test_the_pages_are_not_offered_to_search_engines_yet(template):
    assert '<meta name="robots" content="noindex">' in template


@pytest.mark.parametrize("template", [PAGE, LIST])
def test_no_em_dashes_in_the_copy(template):
    assert "—" not in template and "–" not in template


def test_the_page_needs_no_script_to_be_read():
    """X's crawler does not run one, so the name has to be in the page."""
    assert "<script" not in PAGE and "<script" not in LIST


# --- the card --------------------------------------------------------------------

def size(png: bytes) -> tuple[int, int]:
    from PIL import Image
    return Image.open(io.BytesIO(png)).size


def test_a_guests_card_is_the_size_a_scraper_expects():
    png = og_card.render_guest("Erik Voorhees", "", "June 4, 2026", LONG)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and size(png) == (1200, 630)


@pytest.mark.parametrize("name, role, when, quote", [
    ("", "", "", ""),
    ("A", "", "", "x"),
    ("N" * 400, "R" * 400, "W" * 80, "word " * 400),
    ("Erik\nVoorhees", "tab\there", "June 4, 2026", "a\x00b " * 30),
    ("名前 Ω ✓", "役職", "June 4, 2026", "引用 " * 40),
])
def test_a_card_is_drawn_whatever_it_is_given(name, role, when, quote):
    assert size(og_card.render_guest(name, role, when, quote)) == (1200, 630)


def test_the_cards_words_are_the_name_the_banner_the_day_and_the_lead_line():
    assert guests.card_words(guest(role="CEO")) == ("Erik Voorhees", "CEO", "June 4, 2026", LONG)
    assert guests.card_words({}) == ("", "", "", "")


# --- what is actually shipped ----------------------------------------------------
#
# From here down every test reads the real files. This is the half that
# keeps a name off words that are not that person's.

SHIPPED = json.loads((DATA / "guests.json").read_text())
ROSTER = json.loads((DATA / "guest_roster.json").read_text())
VOICES = json.loads((DATA / "guest_voices.json").read_text())
VERDICTS = json.loads((DATA / "guest_verdicts.json").read_text())
HOSTS = json.loads((DATA / "speaker_map.json").read_text())
PLAYERS = json.loads((DATA / "broadcast_links.json").read_text())
SEEN = {g["slug"]: g for g in ROSTER["guests"]}
EVERY_QUOTE = [(g["slug"], q["id"]) for g in SHIPPED["guests"] for q in g["quotes"]]


def _episodes() -> dict:
    plain = DATA / "episodes.json"
    rows = (json.loads(plain.read_text()) if plain.exists()
            else json.loads(gzip.decompress((DATA / "episodes.json.gz").read_bytes())))
    found: dict = {}
    for row in rows:
        found.setdefault(str(row.get("episode_id")), row)
    return found


@pytest.fixture(scope="module")
def shows():
    return _episodes()


def shipped_guest(slug):
    return next(g for g in SHIPPED["guests"] if g["slug"] == slug)


def shipped_quote(slug, ident):
    return next(q for q in shipped_guest(slug)["quotes"] if q["id"] == ident)


def test_there_are_guests_and_each_has_something_to_read():
    assert len(SHIPPED["guests"]) >= 10
    for g in SHIPPED["guests"]:
        assert 3 <= len(g["quotes"]) <= 8, g["slug"]


def test_every_guest_was_seen_on_camera_by_a_person():
    """The roster is the only way onto a page, and each entry says what
    was on screen when the guest was checked."""
    for g in SHIPPED["guests"]:
        seen = SEEN.get(g["slug"])
        assert seen, f"{g['slug']} is on a page and not in the roster"
        assert len(seen.get("seen") or "") > 30, g["slug"]
        assert re.match(r"\d{4}-\d{2}-\d{2}: ", seen["seen"]), g["slug"]
        assert seen["episode_id"] == g["episode_id"]
        assert seen["name"] == g["name"]
        assert (seen.get("role") or "") == g["role"]
        assert isinstance(seen["anchor"], int) and isinstance(seen["seen_at"], int)


def test_nobody_set_aside_has_a_page():
    names = {g["name"].upper() for g in SHIPPED["guests"]}
    plates = {SEEN[g["slug"]]["plate"].upper() for g in SHIPPED["guests"]}
    for left_out in ROSTER["not_included"]:
        assert left_out["why"]
        assert left_out["plate"].upper() not in names | plates


def test_every_address_is_one_a_path_can_hold_and_no_two_are_the_same():
    slugs = [g["slug"] for g in SHIPPED["guests"]]
    assert all(guests.is_slug(s) for s in slugs)
    assert len(slugs) == len(set(slugs))


def test_the_line_a_guest_was_seen_saying_is_in_that_guests_voice():
    for slug, seen in SEEN.items():
        assert VOICES[slug]["episode_id"] == seen["episode_id"]
        assert seen["anchor"] in VOICES[slug]["lines"], slug


def test_no_line_is_in_two_guests_voices():
    """Three guests share one show. A line belongs to one of them."""
    taken: dict[tuple[str, int], str] = {}
    for slug, voice in VOICES.items():
        for line in voice["lines"]:
            key = (voice["episode_id"], line)
            assert key not in taken, f"{key} is both {taken.get(key)} and {slug}"
            taken[key] = slug


def test_no_line_in_a_guests_voice_is_labelled_as_a_host():
    for slug, voice in VOICES.items():
        named = HOSTS.get(voice["episode_id"], {})
        both = [line for line in voice["lines"] if named.get(str(line))]
        assert not both, f"{slug}: lines {both[:5]} are a host's"


@pytest.mark.parametrize("slug, ident", EVERY_QUOTE)
def test_every_shipped_quote_is_in_the_transcript_word_for_word(slug, ident, shows):
    g, q = shipped_guest(slug), shipped_quote(slug, ident)
    eid, line = ident.rsplit(":", 1)
    assert eid == g["episode_id"]
    segments = shows[eid]["segments"]
    span = calls.locate(q["quote"], segments, int(line))
    assert span is not None, "the quote is not in the transcript"
    assert span.first == int(line), "the quote does not start where its id says"
    assert calls.cut(segments, span) == q["quote"], "the words were changed"


@pytest.mark.parametrize("slug, ident", EVERY_QUOTE)
def test_every_shipped_quote_is_in_that_guests_voice(slug, ident, shows):
    g, q = shipped_guest(slug), shipped_quote(slug, ident)
    segments = shows[g["episode_id"]]["segments"]
    span = calls.locate(q["quote"], segments, int(ident.rsplit(":", 1)[1]))
    voice = set(VOICES[slug]["lines"])
    named = HOSTS.get(g["episode_id"], {})
    assert span.first in voice and span.last in voice
    assert not [i for i in range(span.first, span.last + 1) if named.get(str(i))]
    assert guests.in_voice(voice, named, span.first, span.last)


@pytest.mark.parametrize("slug, ident", EVERY_QUOTE)
def test_every_shipped_quote_would_be_accepted_again_today(slug, ident, shows):
    """The whole gate, run on what shipped: the same answer or a red test."""
    g, q = shipped_guest(slug), shipped_quote(slug, ident)
    again, why = guests.accept(
        {"line": int(ident.rsplit(":", 1)[1]), "quote": q["quote"], "about": q["about"]},
        shows[g["episode_id"]], set(VOICES[slug]["lines"]), HOSTS.get(g["episode_id"], {}))
    assert again is not None, why
    assert (again["id"], again["quote"]) == (ident, q["quote"])


@pytest.mark.parametrize("slug, ident", EVERY_QUOTE)
def test_every_quote_links_to_the_second_it_starts(slug, ident, shows):
    g, q = shipped_guest(slug), shipped_quote(slug, ident)
    line = int(ident.rsplit(":", 1)[1])
    said_at = float(shows[g["episode_id"]]["segments"][line]["t"])
    assert q["start_seconds"] == said_at
    assert q["link"] == f"{PLAYERS[g['episode_id']]}?t={int(said_at)}"
    assert guests.player(q["link"]) == q["link"]
    hours, rest = divmod(int(said_at), 3600)
    stamp = f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"
    assert q["timestamp"] == stamp


@pytest.mark.parametrize("slug, ident", EVERY_QUOTE)
def test_every_quote_was_kept_by_a_person(slug, ident):
    verdict = VERDICTS.get(slug, {}).get(ident)
    assert verdict and verdict.get("keep") is True
    assert verdict["about"] == shipped_quote(slug, ident)["about"]


@pytest.mark.parametrize("slug, ident", EVERY_QUOTE)
def test_every_label_says_what_a_quote_is_about_and_nothing_about_the_person(slug, ident):
    about = shipped_quote(slug, ident)["about"]
    assert 3 <= len(about) <= 60
    assert "—" not in about and "–" not in about
    assert not about.endswith(".")
    # A name does not say which of these fits, so a label uses none.
    assert not re.search(r"\b(he|his|him|she|her|hers)\b", about, re.I), about


def test_a_guest_has_one_quote_they_are_introduced_by():
    for g in SHIPPED["guests"]:
        assert sum(1 for q in g["quotes"] if q.get("lead")) == 1, g["slug"]


def test_a_guests_quotes_are_in_the_order_they_were_said_and_apart():
    for g in SHIPPED["guests"]:
        lines = [int(q["id"].rsplit(":", 1)[1]) for q in g["quotes"]]
        assert lines == sorted(lines) and len(set(lines)) == len(lines), g["slug"]
        assert min(b - a for a, b in zip(lines, lines[1:], strict=False)) > 4, g["slug"]


def test_when_a_guest_was_on_comes_from_their_own_lines(shows):
    for g in SHIPPED["guests"]:
        episode = shows[g["episode_id"]]
        first, last = guests.on_air(set(VOICES[g["slug"]]["lines"]), episode["segments"])
        assert (g["on_from"], g["on_to"]) == (first, last)
        assert first < last
        for q in g["quotes"]:
            assert first <= q["start_seconds"] <= last
        assert g["link"] == f"{PLAYERS[g['episode_id']]}?t={max(0, int(first) - 6)}"
        assert guests.player(g["link"]) == g["link"]
        assert g["lines"] == len(VOICES[g["slug"]]["lines"]) >= 60


def test_the_day_is_the_day_the_show_went_out_in_los_angeles(shows):
    from zoneinfo import ZoneInfo
    for g in SHIPPED["guests"]:
        episode = shows[g["episode_id"]]
        when = calls.aired(g["episode_id"], str(episode.get("published_at") or ""))
        assert g["date"] == when.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
        assert g["show"] == " ".join(str(episode["title"]).split())


def test_two_guests_on_one_show_were_not_on_at_the_same_time():
    by_show: dict[str, list] = {}
    for g in SHIPPED["guests"]:
        by_show.setdefault(g["episode_id"], []).append(g)
    for together in by_show.values():
        together.sort(key=lambda g: g["on_from"])
        for earlier, later in zip(together, together[1:], strict=False):
            assert earlier["on_to"] < later["on_from"], (earlier["slug"], later["slug"])


def test_every_real_page_is_written_whole():
    for g in SHIPPED["guests"]:
        out = guests.render_page(PAGE, guests.page(SHIPPED, g["slug"]))
        assert "{{" not in out
        assert out.count('<article class="said">') == len(g["quotes"])
        assert out.count('class="watch"') == len(g["quotes"])
        assert f"<h1>{g['name']}" in out
    listed = guests.render_index(LIST, SHIPPED)
    assert listed.count('class="guest"') == len(SHIPPED["guests"])


# --- the routes ------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app import main
    with TestClient(main.app) as found:
        yield found


def test_the_list_is_served_as_data(client):
    reply = client.get("/v1/guests")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    body = reply.json()
    assert [g["slug"] for g in body["guests"]] == [g["slug"] for g in
                                                   guests.index(SHIPPED)["guests"]]


def test_one_guest_is_served_as_data(client):
    reply = client.get("/v1/guests/erik-voorhees")
    assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
    body = reply.json()
    assert body["name"] == "Erik Voorhees" and len(body["quotes"]) >= 3
    assert all(guests.player(q["link"]) for q in body["quotes"])


@pytest.mark.parametrize("slug", ["nobody", "Erik-Voorhees", "erik_voorhees", "a" * 300,
                                  "erik-voorhees.json", "%2e%2e"])
def test_a_name_nobody_has_is_not_found(client, slug):
    assert client.get(f"/v1/guests/{slug}").status_code == 404
    assert client.get(f"/og/guest/{slug}.png").status_code == 404


def test_the_list_page_is_served_at_both_of_its_addresses(client):
    for path in ("/guests", "/demo/guests.html"):
        reply = client.get(path)
        assert reply.status_code == 200
        assert reply.headers["content-type"].startswith("text/html")
        assert reply.headers["cache-control"] == "no-store"
        assert "{{" not in reply.text
        assert reply.text.count('class="guest"') == len(SHIPPED["guests"])


def test_the_template_is_never_served_as_a_page(client):
    reply = client.get("/demo/guest.html", follow_redirects=False)
    assert reply.status_code == 302 and reply.headers["location"] == "/guests"


@pytest.mark.parametrize("slug", [g["slug"] for g in SHIPPED["guests"]])
def test_every_guest_has_a_page_and_a_card(client, slug):
    g = shipped_guest(slug)
    page = client.get(f"/guests/{slug}")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "{{" not in page.text
    assert f'content="https://search.lexthedev.com/og/guest/{slug}.png"' in page.text
    for q in g["quotes"]:
        assert f'href="{q["link"]}"' in page.text
    card = client.get(f"/og/guest/{slug}.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"
    assert size(card.content) == (1200, 630)
    again = client.get(f"/og/guest/{slug}.png", headers={"if-none-match": card.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


def test_a_page_for_nobody_is_a_404_that_still_shows_the_list(client):
    reply = client.get("/guests/nobody-at-all")
    assert reply.status_code == 404
    assert reply.text.count('class="guest"') == len(SHIPPED["guests"])


def test_a_name_cannot_carry_markup_into_a_page(client):
    reply = client.get("/guests/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert reply.status_code == 404 and "<script>alert" not in reply.text


def test_with_no_file_the_pages_say_so_and_nothing_falls_over(client, monkeypatch, tmp_path):
    from app import main
    monkeypatch.setattr(main, "_GUESTS_FILE", tmp_path / "nothing.json")
    for path in ("/v1/guests", "/guests"):
        assert client.get(path).status_code == 503
    assert client.get("/v1/guests/erik-voorhees").status_code == 404
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(main, "_GUESTS_FILE", broken)
    monkeypatch.setattr(main, "_GUESTS_READ", (0.0, {}))
    for path in ("/v1/guests", "/guests", "/guests/erik-voorhees"):
        assert client.get(path).status_code == 503
    monkeypatch.setattr(main, "_GUESTS_READ", (0.0, {}))


def test_a_card_that_cannot_be_drawn_falls_back_to_the_shared_one(client, monkeypatch):
    from app import main

    def broken(*_):
        raise OSError("no fonts")
    monkeypatch.setattr(main.og_card, "render_guest", broken)
    monkeypatch.setattr(main, "_GUEST_CARDS", {})
    reply = client.get("/og/guest/mayne.png", follow_redirects=False)
    assert reply.status_code == 302 and reply.headers["location"] == "/demo/og-guests.png"


def test_the_pages_are_not_linked_from_anywhere_yet():
    """They go up for a look first. Linking them is a decision, and this
    test is the reminder to make it on purpose."""
    for name in ("podcast.html", "home.html", "calls.html", "posts.html", "llms.txt"):
        assert "/guests" not in (ROOT / "demo" / name).read_text(), name

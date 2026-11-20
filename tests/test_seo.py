"""What search engines are told: robots.txt, the sitemap, the address each
page is indexed at, and which pages stay out."""

import json
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app import main, seo
from app import traffic as tr

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
LISTED = [name for name, _ in seo.PAGES]
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
NEWEST = {"mcg": "2026-11-15", "threadguy": "2026-11-13", "rasmr": "2026-11-14",
          "elon": "2026-10-20", "tradfi": "2026-10-28", "podcast": "2026-11-05"}


def page(name: str) -> str:
    return (DEMO / name).read_text()


def tag(html: str, pattern: str) -> list[str]:
    return [" ".join(m.split()) for m in re.findall(pattern, html, re.I | re.S)]


def described(html: str) -> list[str]:
    return tag(html, r'<meta name="description" content="([^"]*)">')


def canonicals(html: str) -> list[str]:
    return tag(html, r'<link rel="canonical" href="([^"]*)">')


def hidden(html: str) -> bool:
    return bool(re.search(r'<meta name="robots" content="[^"]*noindex', html, re.I))


def urls(xml: str) -> list[tuple[str, str]]:
    root = ElementTree.fromstring(xml)
    return [(u.findtext("s:loc", "", NS), u.findtext("s:lastmod", "", NS))
            for u in root.findall("s:url", NS)]


# --- robots.txt ------------------------------------------------------------------

def test_robots_lets_everything_be_read_and_names_the_sitemap():
    lines = [ln.strip() for ln in seo.robots_txt().splitlines()
             if ln.strip() and not ln.startswith("#")]
    assert lines == ["User-agent: *", "Disallow:",
                     "Sitemap: https://search.lexthedev.com/sitemap.xml"]


def test_no_page_is_blocked_because_a_blocked_page_cannot_say_noindex():
    """Six pages keep themselves out of search with a tag. A crawler that
    may not fetch them never reads it."""
    assert not re.search(r"(?im)^disallow:[ \t]*\S", seo.robots_txt())
    assert sum(hidden(page(p.name)) for p in DEMO.glob("*.html")) >= 6


# --- the sitemap -----------------------------------------------------------------

def test_the_sitemap_lists_each_page_once_at_the_address_it_names_itself():
    found = urls(seo.sitemap_xml(NEWEST))
    assert [loc for loc, _ in found] == [f"{seo.SITE}/demo/{name}" for name in LISTED]
    assert len(set(found)) == len(found)
    for (loc, _), name in zip(found, LISTED, strict=True):
        assert canonicals(page(name)) == [loc], name


def test_a_page_is_dated_by_the_newest_recording_in_its_archive():
    found = dict(urls(seo.sitemap_xml(NEWEST)))
    assert found[f"{seo.SITE}/demo/mcg.html"] == "2026-11-15"
    assert found[f"{seo.SITE}/demo/mcg-assets.html"] == "2026-11-15"
    assert found[f"{seo.SITE}/demo/podcast.html"] == "2026-11-05"
    assert found[f"{seo.SITE}/demo/finance.html"] == "2026-10-28"
    assert found[f"{seo.SITE}/demo/home.html"] == "2026-11-15"       # newest of all
    assert found[f"{seo.SITE}/demo/how-it-works.html"] == ""         # not an archive


@pytest.mark.parametrize("newest", [None, {}, {"mcg": ""}, {"mcg": "last week"},
                                    {"mcg": None}, {"nobody": "2026-11-15"}])
def test_a_date_that_is_missing_or_not_a_date_is_left_out_and_not_guessed(newest):
    found = urls(seo.sitemap_xml(newest))
    assert len(found) == len(LISTED)
    assert dict(found)[f"{seo.SITE}/demo/mcg.html"] == ""


def test_pages_written_on_the_server_are_listed_after_the_rest_once_each():
    more = [(f"{seo.SITE}/episodes", "2026-11-18"),
            (f"{seo.SITE}/episodes/x-2115904746355851479", "2026-10-29"),
            (f"{seo.SITE}/episodes/x-2115904746355851479", "2026-10-29"),     # twice
            (f"{seo.SITE}/demo/mcg.html", "2026-01-01"),                      # already listed
            ("https://elsewhere.example/episodes/abc", "2026-10-29"),         # not this site
            (f"{seo.SITE}/episodes/dQw4w9WgXcQ", "sometime")]                 # no date
    found = urls(seo.sitemap_xml(NEWEST, more))
    assert [loc for loc, _ in found][:len(LISTED)] == [f"{seo.SITE}/demo/{n}" for n in LISTED]
    assert found[len(LISTED):] == [(f"{seo.SITE}/episodes", "2026-11-18"),
                                   (f"{seo.SITE}/episodes/x-2115904746355851479", "2026-10-29"),
                                   (f"{seo.SITE}/episodes/dQw4w9WgXcQ", "")]
    assert dict(found)[f"{seo.SITE}/demo/mcg.html"] == "2026-11-15"


def test_an_address_with_an_ampersand_is_still_xml():
    found = urls(seo.sitemap_xml(None, [(f"{seo.SITE}/episodes?a=1&b=2", "2026-11-18")]))
    assert found[-1][0] == f"{seo.SITE}/episodes?a=1&b=2"


def test_a_timestamp_is_cut_to_its_day():
    found = dict(urls(seo.sitemap_xml({"mcg": "2026-11-15T21:04:00+00:00"})))
    assert found[f"{seo.SITE}/demo/mcg.html"] == "2026-11-15"


# --- every page on file ------------------------------------------------------------

# Has a title and is not a page of the site: a stray demo, and the two
# templates the episode pages are written from (their routes send anybody
# who opens one to /episodes).
NOT_A_PAGE = {"index.html", "episode.html", "episodes.html"}


def test_every_listed_page_exists_is_not_hidden_and_says_what_it_is():
    titles = []
    for name in LISTED:
        html = page(name)
        assert not hidden(html), f"{name} is in the sitemap and says noindex"
        title = tag(html, r"<title>(.*?)</title>")
        assert len(title) == 1 and title[0], name
        titles.append(title[0])
        said = described(html)
        assert len(said) == 1, name
        # Long enough to say something; a search result prints the
        # first 160 characters or so and cuts the rest.
        assert 60 <= len(said[0]) <= 300, (name, len(said[0]))
    assert len(set(titles)) == len(titles)


def test_every_page_on_file_has_been_decided_on():
    """A page with a title is listed here, hides itself, or is named as
    not a page. Launching one by deleting its noindex tag is not enough:
    until it is listed it is still sent the noindex header, and this fails."""
    for path in sorted(DEMO.glob("*.html")):
        html = path.read_text()
        if not re.search(r"<title>", html, re.I):
            continue                                # a share-card template
        listed = seo.canonical(path.name) is not None
        assert listed or hidden(html) or path.name in NOT_A_PAGE, path.name
        assert not (listed and hidden(html)), path.name


def test_a_page_names_one_address_and_only_a_page_that_is_indexed_names_one():
    for path in sorted(DEMO.glob("*.html")):
        html = path.read_text()
        if "{{" in html:
            continue                    # a template, filled in by its route
        want = seo.canonical(path.name)
        if hidden(html):
            continue                    # its own business; it is not indexed
        assert canonicals(html) == ([want] if want else []), path.name


def test_the_other_tools_are_indexed_at_their_own_address_and_not_listed():
    assert seo.canonical("concierge.html") == "https://concierge.lexthedev.com/demo/concierge.html"
    assert not set(seo.ELSEWHERE) & set(LISTED)
    assert seo.canonical("stats-card.html") is None
    assert seo.canonical("polymarket.html") is None


# --- numbers in what a search result prints ---------------------------------------

COUNT = re.compile(r"(more than |over )?(\d[\d,.]*) (?:MCG Live )?(episodes|hours|recordings)", re.I)


def claims(name: str) -> list[tuple[str, float, str]]:
    html = page(name)
    said = described(html) + tag(
        html, r'<meta (?:property|name)="(?:og|twitter):description" content="([^"]*)">')
    return [(lead.lower(), float(number.replace(",", "").rstrip(".")), unit.lower())
            for text in said for lead, number, unit in COUNT.findall(text)]


def test_a_count_in_a_description_is_a_floor_and_never_an_exact_figure():
    """mcg.html said "651 episodes, 1,040 hours" for two months after the
    archive passed 700 and 1,100. A typed figure is stale the next
    morning; "more than" stays true while the archive only grows."""
    for name in list(LISTED) + list(seo.ELSEWHERE):
        for lead, number, unit in claims(name):
            assert lead, f"{name}: {number:g} {unit} is typed in as an exact figure"


def test_every_floor_is_under_what_the_archive_holds():
    mcg = main._mcg_episodes()
    held = {"episodes": len(mcg),
            "hours": sum(float(e.get("seconds") or 0) for e in mcg) / 3600}
    seen = 0
    for name in ("mcg.html", "mcg-assets.html"):
        for _, number, unit in claims(name):
            assert number <= held[unit], (name, number, unit, held[unit])
            seen += 1
    assert seen >= 3
    # The front door's is across every archive. These three ship as plain
    # lists and are already enough to cover it.
    hours = sum(float(e.get("seconds") or 0)
                for rows in (mcg, main._threadguy_episodes(), main._rasmr_episodes())
                for e in rows) / 3600
    floors = [number for _, number, unit in claims("home.html") if unit == "hours"]
    assert floors and max(floors) <= hours


# --- which addresses stay out ------------------------------------------------------

@pytest.mark.parametrize("path", ["/demo/podcast.html", "/demo/home.html", "/demo/calls.html",
                                  "/demo/concierge.html", "/", "/home", "/calls", "/guests",
                                  "/episodes", "/episodes/x-2115904746355851479",
                                  "/robots.txt", "/sitemap.xml", "/llms.txt",
                                  "/demo/og-home.png", "/demo/ui.css", "/og/search.png"])
def test_what_may_be_indexed_is_sent_no_header(path):
    assert seo.robots_header(path) is None


@pytest.mark.parametrize("path", ["/demo/stats-card.html", "/demo/x-banner.html", "/demo/index.html",
                                  "/demo", "/demo/", "/demo/a-page-made-tomorrow.html",
                                  "/demo/polymarket.html", "/demo/Podcast.html",
                                  "/v1/calls", "/v1/archives", "/x-bot/status", "/healthz",
                                  "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_everything_else_under_demo_and_the_api_is_noindex(path):
    assert seo.robots_header(path) == "noindex"


# --- as served ---------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    async def sizes(request):
        return {"by": {name: {"last": day} for name, day in NEWEST.items()
                       if name != "podcast"}}

    monkeypatch.setattr(main, "TRAFFIC", tr.Traffic(index=None, salt="s", dimension=4))
    monkeypatch.setattr(main, "_archive_sizes", sizes)
    with TestClient(main.app) as c:
        yield c


def test_a_checker_that_only_asks_if_the_files_are_there_is_told_yes(client):
    assert client.head("/robots.txt").status_code == 200
    assert client.head("/sitemap.xml").status_code == 200


def test_robots_txt_is_served_as_text(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.text == seo.robots_txt()
    assert "max-age=3600" in r.headers["cache-control"]
    assert "x-robots-tag" not in r.headers


def test_the_sitemap_is_served_as_xml_with_the_archives_dates(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    listed = urls(r.text)
    found = dict(listed)
    assert len(found) == len(listed)                    # each address once
    assert list(found)[:len(LISTED)] == [f"{seo.SITE}/demo/{name}" for name in LISTED]
    assert all(loc.startswith(f"{seo.SITE}/") for loc in found)
    assert found[f"{seo.SITE}/demo/mcg.html"] == "2026-11-15"
    # Market Bubble's comes off the episodes on file, not the stand-in.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", found[f"{seo.SITE}/demo/podcast.html"])


def test_the_sitemap_survives_the_archives_not_answering(client, monkeypatch):
    async def nothing(request):
        return None

    def broken():
        raise OSError("episodes.json is gone")

    monkeypatch.setattr(main, "_archive_sizes", nothing)
    monkeypatch.setattr(main, "_episodes_by_id", broken)
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert [day for _, day in urls(r.text)][:len(LISTED)] == [""] * len(LISTED)


def test_a_listed_page_is_served_without_the_header_and_a_template_with_it(client):
    assert "x-robots-tag" not in client.get("/demo/home.html").headers
    assert "x-robots-tag" not in client.get("/demo/podcast.html").headers
    assert client.get("/demo/stats-card.html").headers["x-robots-tag"] == "noindex"
    assert client.get("/demo/").headers["x-robots-tag"] == "noindex"
    assert client.get("/healthz").headers["x-robots-tag"] == "noindex"


def test_the_short_paths_still_move_for_good_to_the_address_the_page_names(client):
    r = client.get("/home", follow_redirects=False)
    assert r.status_code == 301 and "x-robots-tag" not in r.headers
    assert seo.canonical("home.html") == seo.SITE + r.headers["location"]


def test_a_shared_search_is_the_same_page_to_a_search_engine(client):
    """A link with a question in it draws its own card for X, and is
    still one page, at one address, to Google."""
    r = client.get("/demo/podcast.html?q=what+did+ansem+say+about+zcash")
    assert canonicals(r.text) == [f"{seo.SITE}/demo/podcast.html"]
    assert "q=what" in tag(r.text, r'<meta property="og:url" content="([^"]*)">')[0]


def test_the_site_gives_its_own_name():
    """Google had been printing "Market Bubble" beside every page, which
    is the show's name and not this site's."""
    html = page("podcast.html")
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert len(blocks) == 1
    told = json.loads(blocks[0])
    assert told["@type"] == "WebSite" and told["name"] == "Market Bubble Search"
    assert told["url"] == f"{seo.SITE}/"
    assert '<meta property="og:site_name" content="Market Bubble Search">' in html


def test_a_crawler_fetching_these_is_not_a_page_view():
    assert not tr.PAGES.match("/robots.txt") and not tr.PAGES.match("/sitemap.xml")

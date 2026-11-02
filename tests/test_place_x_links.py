"""scripts/place_x_links.py: reading the second off our link posts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import place_x_links as links  # noqa: E402
import pytest  # noqa: E402

BROADCAST = "https://x.com/i/broadcasts/1pJdRDqeOYnKW"


def post(pid, urls=(), note_urls=()):
    body = {"id": pid, "entities": {"urls": [{"expanded_url": u} for u in urls]}}
    if note_urls:
        body["note_tweet"] = {"entities": {"urls": [{"expanded_url": u} for u in note_urls]}}
    return body


def test_a_long_posts_link_is_read_from_note_tweet():
    """Most of ours are long, and their links are only there: the Mando
    post, 2 Nov 2026."""
    assert links.link_of(post("1", note_urls=[f"{BROADCAST}?t=6007"])) == \
        {"broadcast": BROADCAST, "t": 6007}


@pytest.mark.parametrize("url,expected", [
    (f"{BROADCAST}?t=481", {"broadcast": BROADCAST, "t": 481}),
    ("https://twitter.com/i/broadcasts/1pJdRDqeOYnKW?t=5&s=20", {"broadcast": BROADCAST, "t": 5}),
    (BROADCAST, None),                                   # no second: nothing to point at
    (f"{BROADCAST}?t=abc", None),
    (f"{BROADCAST}?t=999999", None),
    ("https://x.com/MarketBubble/status/2115904746355851479?t=60", None),
    ("https://evil.example/i/broadcasts/1pJdRDqeOYnKW?t=60", None),
    ("https://www.youtube.com/watch?v=EOkDv8qm8ik&t=60s", {"youtube": "EOkDv8qm8ik", "t": 60}),
    ("https://youtube.com/watch?v=EOkDv8qm8ik&t=1129", {"youtube": "EOkDv8qm8ik", "t": 1129}),
    ("https://youtu.be/O4wBUysNe2k?t=18m49s", {"youtube": "O4wBUysNe2k", "t": 1129}),
    ("https://www.youtube.com/watch?v=EOkDv8qm8ik&t=1h2m3s", {"youtube": "EOkDv8qm8ik", "t": 3723}),
    ("https://www.youtube.com/watch?v=EOkDv8qm8ik", None),          # no second
    ("https://www.youtube.com/watch?v=EOkDv8qm8ik&t=abc", None),
    ("https://www.youtube.com/watch?v=EOkDv8qm8ik&t=99999999", None),
    ("https://www.youtube.com/watch?v=short&t=60s", None),
    ("https://www.youtube.com/watch?v=../../etc/a&t=60s", None),
    ("http://www.youtube.com/watch?v=EOkDv8qm8ik&t=60s", None),
    ("https://www.youtube.com.evil.example/watch?v=EOkDv8qm8ik&t=60s", None),
    ("https://evil.example/watch?v=EOkDv8qm8ik&t=60s", None),
    ("https://www.youtube.com/embed/EOkDv8qm8ik?t=60", None),
])
def test_only_a_show_link_with_a_second_counts(url, expected):
    assert links.link_of(post("1", urls=[url])) == expected


def test_a_broadcast_wins_over_a_youtube_link_in_the_same_post():
    """It opens on X, where the show aired."""
    got = links.link_of(post("1", urls=["https://www.youtube.com/watch?v=EOkDv8qm8ik&t=60s",
                                        f"{BROADCAST}?t=57"]))
    assert got == {"broadcast": BROADCAST, "t": 57}


def test_the_first_broadcast_link_with_a_second_wins():
    got = links.link_of(post("1", urls=["https://example.com", BROADCAST],
                             note_urls=[f"{BROADCAST}?t=9", f"{BROADCAST}?t=10"]))
    assert got == {"broadcast": BROADCAST, "t": 9}


def test_a_run_keeps_what_it_had_adds_what_it_found_and_remembers_how_far(tmp_path, monkeypatch):
    out, searched = tmp_path / "x_links.json", tmp_path / "x_links_search.json"
    out.write_text(json.dumps({"100000": {"broadcast": BROADCAST, "t": 1}}))
    searched.write_text(json.dumps({"newest": "100000"}))
    asked = []

    async def fake(since_id, since_day):
        asked.append((since_id, since_day))
        return [post("100003", urls=["https://youtu.be/O4wBUysNe2k?t=9"]),
                post("100002", note_urls=[f"{BROADCAST}?t=60"]), post("100001", urls=["https://a.b"])]
    monkeypatch.setattr(links, "OUT", out)
    monkeypatch.setattr(links, "SEARCHED", searched)
    monkeypatch.setattr(links, "timeline", fake)
    monkeypatch.setattr(sys, "argv", ["place_x_links.py", "--search"])
    assert links.main() == 0
    assert asked == [("100000", None)]
    assert json.loads(out.read_text()) == {"100000": {"broadcast": BROADCAST, "t": 1},
                                           "100002": {"broadcast": BROADCAST, "t": 60},
                                           "100003": {"youtube": "O4wBUysNe2k", "t": 9}}
    assert json.loads(searched.read_text()) == {"newest": "100003"}


def test_it_reads_every_post_of_ours_but_reposts():
    """Replies too: the receipts under other people's posts and the bot's
    answers link the moments they quote. X's search let 39 of our October
    replies through and dropped 27 (2 Nov 2026)."""
    assert links.EXCLUDE == "retweets"


def test_the_timeline_is_asked_by_numeric_account_id_only(monkeypatch):
    import asyncio

    from app import config

    class Settings:
        x_bot_user_id = "../users/me"
        x_api_key = x_api_secret = x_access_token = x_access_secret = "k"
    monkeypatch.setattr(config, "get_settings", lambda: Settings())
    with pytest.raises(RuntimeError, match="numeric id"):
        asyncio.run(links.timeline(None, None))


def test_the_timeline_follows_pages_with_its_own_token_name(monkeypatch):
    """A timeline pages with pagination_token; search's next_token is
    refused there."""
    import asyncio

    import httpx

    from app import config

    class Settings:
        x_bot_user_id = "2091231768200499200"
        x_api_key = x_api_secret = x_access_token = x_access_secret = "k"
    monkeypatch.setattr(config, "get_settings", lambda: Settings())
    asked = []
    pages = [{"data": [{"id": "2"}], "meta": {"next_token": "abc"}}, {"data": [{"id": "1"}], "meta": {}}]

    def answer(request):
        asked.append(dict(request.url.params))
        assert request.url.path == "/2/users/2091231768200499200/tweets"
        return httpx.Response(200, json=pages[len(asked) - 1])
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    assert asyncio.run(links.timeline("1", None)) == [{"id": "2"}, {"id": "1"}]
    assert asked[0]["since_id"] == "1" and asked[0]["exclude"] == "retweets"
    assert "pagination_token" not in asked[0] and asked[1]["pagination_token"] == "abc"
    assert "note_tweet" in asked[0]["tweet.fields"]

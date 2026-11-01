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
    ("https://www.youtube.com/watch?v=EOkDv8qm8ik&t=60s", None),
])
def test_only_a_broadcast_link_with_a_second_counts(url, expected):
    assert links.link_of(post("1", urls=[url])) == expected


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
        return [post("100002", note_urls=[f"{BROADCAST}?t=60"]), post("100001", urls=["https://a.b"])]
    monkeypatch.setattr(links, "OUT", out)
    monkeypatch.setattr(links, "SEARCHED", searched)
    monkeypatch.setattr(links, "search", fake)
    monkeypatch.setattr(sys, "argv", ["place_x_links.py", "--search"])
    assert links.main() == 0
    assert asked == [("100000", None)]
    assert json.loads(out.read_text()) == {"100000": {"broadcast": BROADCAST, "t": 1},
                                           "100002": {"broadcast": BROADCAST, "t": 60}}
    assert json.loads(searched.read_text()) == {"newest": "100002"}


def test_it_reads_only_our_own_original_posts():
    assert links.QUERY == "from:mbubbleSearch -is:retweet -is:reply"

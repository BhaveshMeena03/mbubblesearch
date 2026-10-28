"""ThreadGuy's episode notes: written by a script, served by one route,
drawn under whichever player is showing the episode.

The notes promise a second for every topic, so most of what is checked
here is that a time the episode does not have never reaches the page, that
a topic sits where it was said, and that nothing a model wrote goes into
the page as markup.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main as main_module  # noqa: E402
from scripts import summarize_threadguy as summ  # noqa: E402

PAGE = (ROOT / "demo" / "threadguy.html").read_text()


def seg(t, text):
    return {"t": float(t), "text": text}


# The 22 Oct 2026 market open, in short. Asked for seconds, the model put
# the 70% line (0:29:04) at 0:35:54, Matthew Prince (0:42:52) at 1:01:38
# and the guest's certificate bug (1:42:36) at 2:15:58.
SEGMENTS = [
    seg(204, "Welcome back to the stream, a lot to cover today."),
    seg(392, "Back in 2006 Brian Hunter at Amaranth bet the fund on natural gas spreads."),
    seg(1638, "OpenAI's Patch the Planet effort found the PHP bug about 60 days ago."),
    seg(1744, "PHP runs about 70% of the internet."),
    seg(2154, "Look at this dashboard, exploit attempts are going vertical."),
    seg(2572, "Cloudflare's Matthew Prince says attempts went from thousands to millions."),
    seg(4458, "This guy says Bitcoin is capped and could go to zero."),
    seg(4626, "Andrew Curran says banks are finding AI agents inside customer accounts."),
    seg(5940, "Welcome Tay, our cybersecurity expert, to the stream."),
    seg(6156, "There was also a certificate bug where a firm's private key leaked."),
    seg(8760, "That's the stream, see you tomorrow."),
]
TLDR = "ThreadGuy covers the PHP exploit that hit markets, then a cybersecurity expert explains it."
GIVEN = "ThreadGuy MARKET OPEN: AI BROKE THE INTERNET"
GOOD = [("0:06:32", "He tells the story of Brian Hunter at Amaranth betting on natural gas in 2006."),
        ("0:27:18", "OpenAI's Patch the Planet effort found the PHP bug about 60 days ago."),
        ("0:42:52", "Cloudflare's Matthew Prince says attempts went from thousands to millions."),
        ("1:17:06", "Andrew Curran reports banks finding AI agents inside customer accounts."),
        ("1:42:36", "The guest explains a certificate bug where a firm's private key leaked.")]


def reply(topics, tldr=TLDR):
    return "here you go\n" + json.dumps(
        {"tldr": tldr, "topics": [{"at": at, "text": text} for at, text in topics]})


def check(topics, tldr=TLDR, keep=False, given=GIVEN):
    return summ.check(reply(topics, tldr), SEGMENTS, keep_what_passes=keep, given=given)


def test_the_prompt_asks_for_the_printed_timestamp_not_seconds():
    assert '"at": "the timestamp of the line the topic starts on, copied' in summ.SYSTEM
    assert '"t":' not in summ.SYSTEM and "seconds where" not in summ.SYSTEM


def test_the_timestamp_is_copied_and_the_sum_is_done_here():
    assert summ.seconds("1:01:38") == 3698 and summ.seconds("[2:15:58]") == 8158
    assert summ.seconds("42:52") is None and summ.seconds("2572") is None
    assert summ.seconds("soon") is None


def test_a_good_reply_passes_whole():
    got, dropped = check(GOOD)
    assert [t["t"] for t in got["topics"]] == [392, 1638, 2572, 4626, 6156]
    assert dropped == []


def test_a_timestamp_a_few_seconds_off_is_the_line_it_meant():
    got, _ = check([("0:42:49", GOOD[2][1])] + GOOD[:2] + GOOD[3:])
    assert 2572 in [t["t"] for t in got["topics"]]


def test_a_time_the_transcript_has_no_line_at_is_refused():
    """1:01:38 is where the model put Prince; nothing was said there."""
    with pytest.raises(ValueError, match="no line"):
        check(GOOD[:2] + [("1:01:38", GOOD[2][1])] + GOOD[3:])
    with pytest.raises(ValueError, match="no line"):
        check(GOOD + [("9:59:59", "A topic past the end of the stream.")])


def test_a_topic_on_the_wrong_stretch_is_refused():
    """A real line, but the figure was said seven minutes earlier."""
    late = GOOD + [("0:35:54", "PHP runs about 70% of the internet.")]
    with pytest.raises(ValueError, match="not in the transcript near 0:35:54"):
        check(late)
    named = GOOD[:2] + [("1:14:18", "Cloudflare's Matthew Prince on exploit attempts.")] + GOOD[3:]
    with pytest.raises(ValueError, match="not in the transcript near 1:14:18"):
        check(named)


def test_the_second_try_keeps_what_passes():
    mixed = GOOD + [("0:35:54", "PHP runs about 70% of the internet.")]
    got, dropped = check(mixed, keep=True)
    assert len(got["topics"]) == 5 and len(dropped) == 1 and "70" in dropped[0]


def test_the_tldr_is_never_let_off():
    with pytest.raises(ValueError, match="tldr"):
        check(GOOD, tldr=TLDR + " Bitcoin fell to $84,000.", keep=True)


def test_the_host_and_the_title_need_not_be_said():
    """ThreadGuy is the one talking, so the transcript never names him."""
    hosted = GOOD[:4] + [("1:42:36", "The guest walks ThreadGuy through a certificate bug.")]
    with pytest.raises(ValueError, match="ThreadGuy"):
        check(hosted, given="")
    got, _ = check(hosted)
    assert len(got["topics"]) == 5


def test_numbers_compare_as_values():
    assert summ._numbers("6,182 at $29.50 and 4.0") == {"6182", "29.5", "4"}
    assert summ.missing_numbers("up $360K on it", "he is up 360,000 dollars") == []
    assert summ.missing_numbers("a $1.5M raise", "we raised 1.5 million") == []
    assert summ.missing_numbers("yields near 4.98%", "yields near 4.95%") == ["4.98"]


def test_a_name_the_captions_split_is_the_same_name():
    said = "i bought it on robin hood and asked chat gpt about meta dao"
    assert summ.missing_names("he bought on Robinhood, asked ChatGPT about MetaDAO", said) == []
    assert summ.missing_names("he asked Grok", said) == ["Grok"]


def test_plurals_and_possessives_are_not_new_names():
    said = "the dao voted. hyperliquid's book. two atlas agents"
    assert summ.missing_names("three DAOs and Hyperliquid's order book", said) == []
    assert summ.missing_names("Atlas runs the agents", "atlas runs") == []


def test_two_topics_on_the_same_stretch_are_one():
    got, _ = check(GOOD[:3] + [("0:43:02", "Prince again, a few seconds on.")] + GOOD[3:])
    assert [t["t"] for t in got["topics"]] == [392, 1638, 2572, 4626, 6156]


def test_too_few_topics_is_refused():
    with pytest.raises(ValueError, match="usable topics"):
        check(GOOD[:3], keep=True)


def test_no_dashes_reach_the_page():
    dashed = [(at, text.replace(" at ", " — at ", 1)) for at, text in GOOD]
    got, _ = check(dashed, tldr="ThreadGuy covers the PHP exploit – then a cybersecurity expert.")
    assert "—" not in json.dumps(got, ensure_ascii=False)
    assert "–" not in json.dumps(got, ensure_ascii=False)


class Replies:
    """A client that answers each call with the next reply, and keeps what
    it was sent."""

    def __init__(self, *raws):
        self.raws, self.sent = list(raws), []
        self.messages = self

    def stream(self, **kwargs):
        client = self
        client.sent.append(kwargs)

        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get_final_message(self):
                text = client.raws.pop(0)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        return Stream()


ROW = {"seconds": 8800, "title": "MARKET OPEN: AI BROKE THE INTERNET", "format": "stream"}


def test_a_reply_that_fails_whole_gets_a_second_try_that_keeps_what_passes():
    late = GOOD + [("0:35:54", "PHP runs about 70% of the internet.")]
    client = Replies(reply(late), reply(late))
    got = asyncio.run(summ.summarize(client, "m", ROW, SEGMENTS))
    assert len(client.sent) == 2
    assert [t["t"] for t in got["topics"]] == [392, 1638, 2572, 4626, 6156]
    assert len(got["dropped"]) == 1


def test_two_unusable_replies_are_no_summary():
    client = Replies("not json", reply(GOOD[:2]))
    with pytest.raises(ValueError, match="no usable summary in two tries"):
        asyncio.run(summ.summarize(client, "m", ROW, SEGMENTS))


def test_the_model_reads_the_names_the_site_spells():
    segments = SEGMENTS[:1] + [seg(300, "Salana is pumping today.")] + SEGMENTS[1:]
    client = Replies(reply(GOOD))
    asyncio.run(summ.summarize(client, "m", ROW, segments))
    sent = client.sent[0]["messages"][0]["content"]
    assert "[0:05:00] Solana is pumping today." in sent and "Salana" not in sent


def test_an_incomplete_transcript_is_not_summarized():
    """Six minutes of a three hour stream, summarized, reads as the stream."""
    class Never:
        @property
        def messages(self):
            raise AssertionError("no model call for a broken transcript")
    segs = [{"t": 0.0, "text": "gm"}, {"t": 360.0, "text": "ok"}]
    with pytest.raises(ValueError, match="incomplete transcript"):
        asyncio.run(summ.summarize(Never(), "m", {"seconds": 11220, "title": "x"}, segs))


class Flaky:
    """A client whose stream drops the given number of times, then answers."""

    def __init__(self, drops):
        self.drops, self.calls = drops, 0
        self.messages = self

    def stream(self, **kwargs):
        client = self

        class Stream:
            async def __aenter__(self):
                client.calls += 1
                if client.calls <= client.drops:
                    raise httpx.RemoteProtocolError("peer closed connection")
                return self

            async def __aexit__(self, *a):
                return False

            async def get_final_message(self):
                return SimpleNamespace(content=[])
        return Stream()


def test_a_dropped_call_is_tried_again(monkeypatch):
    async def no_wait(seconds):
        return None
    monkeypatch.setattr(summ.asyncio, "sleep", no_wait)
    client = Flaky(drops=2)
    asyncio.run(summ.ask(client, "m", "u"))
    assert client.calls == 3
    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(summ.ask(Flaky(drops=3), "m", "u"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    notes = {"abc": {"tldr": "t", "topics": [{"t": 5, "text": "x"}],
                     "model": "m", "title": "T", "published_at": "2026-09-30"}}
    path = tmp_path / "s.json.gz"
    path.write_bytes(gzip.compress(json.dumps(notes).encode()))
    monkeypatch.setattr(main_module, "THREADGUY_SUMMARIES", path)
    monkeypatch.setattr(main_module, "_THREADGUY_SUMMARIES_CACHE", None)
    with TestClient(main_module.app) as c:
        yield c
    main_module._THREADGUY_SUMMARIES_CACHE = None


def test_the_route_serves_only_what_the_page_draws(client):
    body = client.get("/v1/threadguy/summaries").json()
    assert body["count"] == 1
    assert body["summaries"]["abc"] == {"tldr": "t", "topics": [{"t": 5, "text": "x"}]}


def test_a_missing_file_is_no_notes_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "THREADGUY_SUMMARIES", tmp_path / "none.gz")
    monkeypatch.setattr(main_module, "_THREADGUY_SUMMARIES_CACHE", None)
    assert main_module._threadguy_summaries() == {}
    main_module._THREADGUY_SUMMARIES_CACHE = None


def test_the_page_keys_notes_by_the_field_the_api_sends():
    """The episodes route sends episode_id. Keyed by e.id, every lookup
    missed and no row ever showed notes, without an error anywhere."""
    assert "NOTES[e.episode_id]" in PAGE
    assert "NOTES[e.id]" not in PAGE


def test_model_text_goes_in_as_text():
    block = PAGE[PAGE.index("function notes(e, frame)"):PAGE.index("function row(e)")]
    assert "innerHTML" not in block
    assert "textContent = sm.tldr" in block and "textContent = t.text" in block


def test_notes_failing_to_load_does_not_stop_the_shelf():
    assert 'fetch("/v1/threadguy/summaries")' in PAGE
    i = PAGE.index('fetch("/v1/threadguy/summaries")')
    assert ".catch(function(){})" in PAGE[i:i + 300]


def test_a_topic_moves_the_player_it_sits_under():
    """Chapters, not a second video: the time sets the src of the frame
    it was given, the shelf row's or the stage's."""
    assert 'notes(e, function(){ return pw.querySelector("iframe"); })' in PAGE
    assert 'notes(e, function(){ return st.querySelector("iframe"); })' in PAGE

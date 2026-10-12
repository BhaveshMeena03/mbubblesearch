"""Questions about now are searched in what is newest.

"What did Ansem pick as his trades this week" was asked two days after
the show in which he named three, and the archive answered from May and
June. The week's passage was in the results; five older ones outranked
it, because nothing in a search by meaning knows when anything was said.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import podcast, recency  # noqa: E402
from app.config import Settings  # noqa: E402
from app.podcast import PodcastIndex, _recent_note  # noqa: E402
from app.terms import TermIndex  # noqa: E402

SHELF = [
    {"episode_id": "may", "published_at": "2026-05-22", "title": "Why Ansem Thinks Ethereum Is Done"},
    {"episode_id": "june", "published_at": "2026-06-04T00:00:00Z", "title": "The Dollar Is Going to Zero"},
    {"episode_id": "x-thursday", "published_at": "2026-10-09", "title": "BULL RUN IS OVER?"},
    {"episode_id": "friday-cut", "published_at": "2026-10-10", "title": "Bull Run Is Over, with Banks"},
]


# --- is the question about now -----------------------------------------------

@pytest.mark.parametrize("question,days", [
    ("what did ansem pick as his trades this week", 7),
    ("what is ansem bullish on right now", 7),
    ("what has threadguy been saying about hype lately", 7),
    ("what did they say on the latest episode", 2),
    ("what's the latest on zcash", 2),
    ("what did banks say last night", 2),
    ("what did he say on today's market open", 2),
    ("recap the last show", 2),
    ("what did ansem say last week", 14),
    ("what has warsh said this month", 31),
    # The tightest phrase wins.
    ("the latest of what he said this month", 2),
])
def test_a_question_about_now_says_how_far_back_it_reaches(question, days):
    assert recency.window_days(question) == days


@pytest.mark.parametrize("question", [
    "what did ansem say about zcash",
    "what did ansem say about solana last cycle",
    "how did he trade the top last year",
    "when was the last time bitcoin dropped 7% in an hour",
    "who said ethereum is done",
    "did he latestage anything",
    "",
])
def test_the_past_is_not_now(question):
    """"Last" is not a word about the present. People say "last cycle"
    constantly, and it must keep searching everything."""
    assert recency.window_days(question) is None


# --- which recordings are the newest -----------------------------------------

def test_the_latest_episode_is_the_broadcast_and_the_cut_of_it():
    """Thursday's show on X and Friday's upload of it are one episode."""
    found = recency.recent(SHELF, 2)
    assert found.ids == ("x-thursday", "friday-cut")
    assert (found.since, found.newest) == ("2026-10-09", "2026-10-10")
    assert found.title == "Bull Run Is Over, with Banks"


def test_now_is_the_newest_recording_and_not_the_calendar():
    """An archive that last grew in June still has a latest episode."""
    old = SHELF[:2]
    assert recency.recent(old, 2).ids == ("june",)
    assert recency.recent(old, 31).ids == ("may", "june")


def test_a_shelf_is_read_whichever_way_it_spells_its_ids():
    rows = [{"id": "a", "published_at": "2026-10-08", "title": "one"},
            {"episode_id": "b", "published_at": "2026-10-09", "title": "two"},
            {"id": "b", "published_at": "2026-10-09", "title": "two"},
            {"id": "undated", "published_at": None, "title": "?"},
            {"id": "junk", "published_at": "soon", "title": "?"}]
    found = recency.recent(rows, 7)
    assert found.ids == ("a", "b")
    assert recency.recent([], 7) is None
    assert recency.recent([{"id": "x", "published_at": ""}], 7) is None


def test_the_model_is_told_what_it_was_shown():
    note = recency.recent(SHELF, 2).note()
    assert "from 2026-10-09 to 2026-10-10" in note
    assert '"Bull Run Is Over, with Banks" (2026-10-10)' in note
    assert "do not" in note and "older view is current" in note
    assert "from 2026-06-04;" in recency.recent(SHELF[:2], 2).note()


# --- the search itself --------------------------------------------------------

class Database:
    """Pinecone, as far as retrieval can tell: it honours the filter."""

    def __init__(self, passages):
        self.passages = passages
        self.filters: list[dict | None] = []

    def query(self, **kwargs):
        narrowed = kwargs.get("filter")
        self.filters.append(narrowed)
        only = set((narrowed or {}).get("episode_id", {}).get("$in", []))
        who = set((narrowed or {}).get("speakers", {}).get("$in", []))
        found = []
        for episode, score, speakers in self.passages:
            if only and episode not in only:
                continue
            if who and not who & set(speakers):
                continue
            found.append(SimpleNamespace(score=score, metadata={
                "episode_id": episode, "title": episode, "text": f"said in {episode}",
                "start_seconds": 60.0, "url": f"https://youtu.be/{episode}",
                "platform": "youtube", "speakers": list(speakers),
                "published_at": "2026-01-01"}))
        return SimpleNamespace(matches=found)


# The scores the real question got: the best of the newest show at 0.424
# against 0.523 for the best in the archive.
OLD_AND_NEW = [("may", 0.523, ["Ansem"]), ("june", 0.51, ["Ansem"]),
               ("x-thursday", 0.424, ["Ansem"]), ("friday-cut", 0.41, ["FaZe Banks"])]


def narrowed_to(index, *ids):
    """The searches that were limited to exactly these recordings."""
    return [f for f in index._index.filters
            if f and f.get("episode_id", {}).get("$in") == list(ids)]


def index_over(passages, shelf=SHELF, monkeypatch=None):
    index = PodcastIndex.__new__(PodcastIndex)
    index._settings = Settings(anthropic_api_key="k", voyage_api_key="k",
                               pinecone_api_key="k", rerank_model=None)
    index._namespace = "podcast"
    index._voyage = None
    index._index = Database(passages)
    index._terms = TermIndex(Path("/nonexistent/terms.json"))
    index._shelf = None
    if shelf is not None:
        async def rows():
            return shelf
        index.follow(rows)

    async def embedded(*args, **kwargs):
        return [0.0]
    monkeypatch.setattr(podcast, "embed_query", embedded)
    return index


@pytest.mark.anyio
async def test_this_week_is_answered_from_this_weeks_show(monkeypatch):
    """The failure itself: two older shows outscore the one that aired."""
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did ansem pick as his trades this week", 6)
    assert {h.episode_id for h in hits} == {"x-thursday", "friday-cut"}
    assert narrowed_to(index, "x-thursday", "friday-cut")
    note = _recent_note(hits)
    assert note and "2026-10-10" in note


@pytest.mark.anyio
async def test_a_question_that_is_not_about_now_searches_everything(monkeypatch):
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did ansem say about zcash", 6)
    assert [h.episode_id for h in hits][:2] == ["may", "june"]
    assert "episode_id" not in (index._index.filters[0] or {})
    assert _recent_note(hits) is None


@pytest.mark.anyio
async def test_the_next_question_does_not_inherit_the_dates(monkeypatch):
    """One task can ask twice: the bot answers mention after mention."""
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    await index._retrieve("what did ansem pick this week", 6)
    hits = await index._retrieve("what did ansem say about zcash", 6)
    assert _recent_note(hits) is None


@pytest.mark.anyio
async def test_nothing_recent_on_the_subject_falls_back_and_says_nothing_about_now(monkeypatch):
    """The newest shows are not near the question at all. Searching
    everything is right, and telling the model the passages are recent
    would be a lie."""
    passages = [("may", 0.71, []), ("x-thursday", 0.01, [])]
    index = index_over(passages, monkeypatch=monkeypatch)
    hits = await index._retrieve("what is the latest on dogecoin", 6)
    assert [h.episode_id for h in hits] == ["may"]
    assert index._index.filters[-1] is None
    assert _recent_note(hits) is None


@pytest.mark.anyio
async def test_a_named_host_and_this_week_are_both_asked_for(monkeypatch):
    passages = OLD_AND_NEW + [("x-thursday", 0.5, ["Ansem"]), ("x-thursday", 0.49, ["Ansem"])]
    index = index_over(passages, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did ansem say this week", 6)
    judged, both = narrowed_to(index, "x-thursday", "friday-cut")
    assert "speakers" not in judged
    assert both["speakers"] == {"$in": ["Ansem"]}
    assert {h.episode_id for h in hits} == {"x-thursday"}


@pytest.mark.anyio
async def test_a_host_with_too_little_this_week_still_gets_this_week(monkeypatch):
    """Banks has one labelled passage in the newest show. The answer is
    the rest of that show, not three passages of him from August."""
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did banks say this week", 6)
    assert {h.episode_id for h in hits} == {"x-thursday", "friday-cut"}
    asked = narrowed_to(index, "x-thursday", "friday-cut")
    assert [("speakers" in f) for f in asked] == [False, True]
    assert _recent_note(hits)


@pytest.mark.anyio
async def test_the_week_is_judged_on_everything_said_and_not_on_one_voice(monkeypatch):
    """What happened on the real archive. The best passage of the week
    was in the upload that has no voice labels yet, so among Ansem's
    labelled passages the week scored 0.364 against 0.523 and lost to
    July. Whether the week is about the question does not depend on who
    is labelled."""
    passages = [("may", 0.523, ["Ansem"]), ("june", 0.51, ["Ansem"]),
                ("friday-cut", 0.424, []),
                ("x-thursday", 0.364, ["Ansem"]), ("x-thursday", 0.36, ["Ansem"]),
                ("x-thursday", 0.35, ["Ansem"])]
    index = index_over(passages, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did ansem pick as his trades this week", 6)
    assert {h.episode_id for h in hits} == {"x-thursday"}
    assert all("Ansem" in h.speakers for h in hits)
    assert _recent_note(hits)


@pytest.mark.anyio
async def test_an_old_passage_with_the_right_word_stays_out(monkeypatch):
    """The word index knows nothing about dates."""
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)

    async def with_an_old_one(query, hits):
        old = hits[0].model_copy(update={"episode_id": "may", "start_seconds": 9.0})
        new = hits[0].model_copy(update={"start_seconds": 99.0})
        return hits + [old, new]
    index._add_exact_matches = with_an_old_one
    hits = await index._retrieve("what did ansem say about starkware this week", 6)
    assert "may" not in {h.episode_id for h in hits}
    assert any(h.start_seconds == 99.0 for h in hits)


@pytest.mark.anyio
async def test_an_archive_with_no_shelf_behaves_as_it_always_did(monkeypatch):
    index = index_over(OLD_AND_NEW, shelf=None, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did ansem pick this week", 6)
    assert [h.episode_id for h in hits][:2] == ["may", "june"]
    assert index._index.filters == [{"speakers": {"$in": ["Ansem"]}}]


@pytest.mark.anyio
async def test_a_shelf_that_cannot_be_read_costs_the_narrowing_and_not_the_answer(monkeypatch):
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)

    async def broken():
        raise TimeoutError("pinecone")
    index.follow(broken)
    hits = await index._retrieve("what is the latest", 6)
    assert hits and _recent_note(hits) is None


@pytest.mark.anyio
async def test_the_note_is_not_claimed_for_passages_from_somewhere_else(monkeypatch):
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did ansem pick this week", 6)
    elsewhere = [hits[0].model_copy(update={"episode_id": "may"})]
    assert _recent_note(elsewhere) is None
    assert _recent_note([]) is None


@pytest.mark.anyio
async def test_the_request_carries_the_note_after_the_question(monkeypatch):
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    hits = await index._retrieve("what did ansem pick this week", 6)
    request = index._build_request("what did ansem pick this week", hits)
    blocks = [b["text"] for b in request["messages"][0]["content"]]
    assert blocks[1] == "what did ansem pick this week"
    assert blocks[-1].startswith("This question is about what is recent.")
    plain = index._build_request("what did ansem say about zcash",
                                 await index._retrieve("what did ansem say about zcash", 6))
    assert not any("about what is recent" in b["text"]
                   for b in plain["messages"][0]["content"])


# --- when the newest recordings are not about the question --------------------

# What each question measured against the real archives on 12 October 2026:
# the search's best score in the newest recordings and in the whole
# archive, then the reranker's. Whether the newest recordings were the
# right place to answer from was read off the passages by hand.
MEASURED = [
    # Market Bubble, one show a week: it is always roughly on its subject.
    ("what did ansem pick as his trades this week", .424, .523, .516, .570, True),
    ("what is ansem's zcash target on the latest episode", .487, .515, .637, .613, True),
    ("what is ansem bullish on right now", .425, .500, .531, .793, True),
    ("what did they say about hyperliquid this week", .476, .564, .535, .762, True),
    # ThreadGuy and MCG, daily.
    ("what has threadguy been saying about bitcoin this week", .487, .563, .586, .676, True),
    ("what did threadguy say about hyperliquid on the latest stream", .510, .575, .660, .762, True),
    ("which projects were on mcg this week", .410, .514, .727, .840, True),
    ("what is the latest on the anthropic s-1", .361, .454, .307, .688, False),
    ("what is the latest on clawpump", .301, .533, .396, .738, False),
    ("what did they say about usepod recently", .455, .483, .357, .668, False),
    # The Record: the newest recording is CZ, whatever is asked.
    ("what did the fed chair say at the latest press conference", .395, .555, .498, .770, False),
    ("what is warsh saying about rate cuts right now", .227, .445, .287, .730, False),
    ("what has jamie dimon said lately about private credit", .305, .555, .326, .793, False),
    ("what did cz say on the latest interview", .352, .396, .629, .652, True),
    # Elon: the newest recording is an interview.
    ("what did elon say in his latest interview", .495, .537, .602, .711, True),
    ("what is the latest on starship", .453, .463, .691, .695, True),
    ("what has elon said recently about optimus", .393, .540, .494, .828, False),
]


@pytest.mark.parametrize("question,near,far,near_read,far_read,here", MEASURED)
def test_the_newest_recordings_are_used_when_they_are_about_it(
        question, near, far, near_read, far_read, here):
    assert recency.is_here(near, far, near_read, far_read) is here, question


def test_without_the_reranker_the_search_decides_alone():
    assert recency.is_here(.424, .523, None, None) is True
    assert recency.is_here(.395, .555, None, None) is False
    assert recency.is_here(0.0, .5, None, None) is False
    assert recency.is_here(.4, 0.0, None, None) is False


RECORD = [
    {"episode_id": "powell-apr", "published_at": "2026-04-29", "subject": "Jerome Powell",
     "title": "Jerome Powell: FOMC Press Conference, April 29, 2026"},
    {"episode_id": "dimon-apr", "published_at": "2026-04-29", "subject": "Jamie Dimon",
     "title": "Live podcast with Jamie Dimon"},
    {"episode_id": "warsh-jul", "published_at": "2026-07-29", "subject": "Kevin Warsh",
     "title": "Kevin Warsh: FOMC Press Conference, July 29, 2026"},
    {"episode_id": "warsh-sep", "published_at": "2026-09-16", "subject": "Kevin Warsh",
     "title": "Kevin Warsh: FOMC Press Conference, September 16, 2026"},
    {"episode_id": "cz-sep", "published_at": "2026-09-25", "subject": "CZ",
     "title": "CZ Binance Founder: This Crypto Bull Run Could Make You Rich"},
]


def test_a_named_person_is_asked_about_in_their_own_recordings():
    def ids(question):
        return [r["episode_id"] for r in recency.named(RECORD, question)]
    assert ids("what did powell say about inflation recently") == ["powell-apr"]
    assert ids("what is Kevin Warsh saying right now") == ["warsh-jul", "warsh-sep"]
    assert ids("what's cz's latest take on bnb") == ["cz-sep"]
    # Nobody on the shelf is named: all of it.
    assert len(ids("what did the fed chair say at the latest press conference")) == 5
    assert len(ids("what did powellton say")) == 5
    # A shelf that files nothing under a person has nothing to match.
    assert recency.named(SHELF, "what did ansem say this week") == SHELF


def test_the_most_recent_recording_on_the_subject_is_found():
    """The Fed question. CZ is newest and scores 0.498; every press
    conference is near 0.77. The newest of those is September's."""
    passed = recency.recent(RECORD, 2)
    assert passed.ids == ("cz-sep",)
    read = [("powell-apr", .770), ("warsh-jul", .762), ("warsh-sep", .700),
            ("cz-sep", .498), ("dimon-apr", .30)]
    found = recency.last_on(read, RECORD, 2, passed)
    assert found.ids == ("warsh-sep",)
    assert found.after == ("CZ Binance Founder: This Crypto Bull Run Could Make You Rich",
                           "2026-09-25")
    note = found.note()
    assert "September 16, 2026" in note and "(2026-09-16)" in note
    assert "most recent the archive has" in note
    assert "Do not describe it as this week's" in note


def test_an_older_recording_has_to_be_on_the_subject_and_not_merely_near_it():
    passed = recency.recent(RECORD, 2)
    # September's conference only brushes the subject: July's is the one.
    read = [("powell-apr", .770), ("warsh-jul", .762), ("warsh-sep", .60)]
    assert recency.last_on(read, RECORD, 2, passed).ids == ("warsh-jul",)


def test_the_window_reaches_as_far_back_from_there_as_the_question_did():
    passed = recency.recent(RECORD, 2)
    read = [("warsh-sep", .77), ("warsh-jul", .76)]
    assert recency.last_on(read, RECORD, 62, passed).ids == ("warsh-jul", "warsh-sep")


def test_the_present_is_given_back_when_it_was_the_answer_after_all():
    """The search scored the newest show low and the reranker, reading
    the words, put it with the best. Nothing was passed over."""
    passed = recency.recent(RECORD, 2)
    read = [("powell-apr", .70), ("cz-sep", .69)]
    assert recency.last_on(read, RECORD, 2, passed) is passed


def test_nothing_that_can_be_placed_in_time_is_nothing():
    passed = recency.recent(RECORD, 2)
    assert recency.last_on([], RECORD, 2, passed) is None
    assert recency.last_on([("gone", .9)], RECORD, 2, passed) is None
    assert recency.last_on([("warsh-sep", 0.0)], RECORD, 2, passed) is None


def reading(scores, monkeypatch):
    """A reranker that scores a passage by the recording it is from."""
    seen = []

    async def read(client, query, documents, *, model):
        seen.append(documents)
        return [scores.get(d.split("\n")[0], 0.0) for d in documents]
    monkeypatch.setattr(podcast, "rerank_scores", read)

    async def order(*args, **kwargs):
        return None
    monkeypatch.setattr(podcast, "rerank_order", order)
    return seen


def record_index(passages, monkeypatch):
    index = index_over(passages, shelf=RECORD, monkeypatch=monkeypatch)
    index._settings = index._settings.model_copy(update={"rerank_model": "reads"})
    index._namespace = "tradfi"
    return index


ON_THE_RECORD = [("powell-apr", .555, []), ("warsh-jul", .55, []), ("warsh-sep", .54, []),
                 ("dimon-apr", .30, []), ("cz-sep", .395, [])]


@pytest.mark.anyio
async def test_the_latest_press_conference_is_not_answered_from_cz(monkeypatch):
    """The failure this was built for. The first version of "now" looked
    only in the newest recording and wrote the answer from CZ."""
    index = record_index(ON_THE_RECORD, monkeypatch)
    reading({"powell-apr": .770, "warsh-jul": .762, "warsh-sep": .70,
             "cz-sep": .498, "dimon-apr": .30}, monkeypatch)
    question = "what did the fed chair say at the latest press conference"
    hits = await index._retrieve(question, 6)
    assert {h.episode_id for h in hits} == {"warsh-sep"}
    note = _recent_note(hits)
    assert "most recent recording that is" in note and "(2026-09-16)" in note
    request = index._build_request(question, hits)
    assert request["messages"][0]["content"][-1]["text"] == note


@pytest.mark.anyio
async def test_a_question_naming_powell_is_searched_in_powells_recordings(monkeypatch):
    index = record_index(ON_THE_RECORD, monkeypatch)
    reading({"powell-apr": .79}, monkeypatch)
    hits = await index._retrieve("what did powell say about inflation recently", 6)
    assert {h.episode_id for h in hits} == {"powell-apr"}
    # His are the only recordings looked in, and his newest is "now".
    assert all(f["episode_id"]["$in"] == ["powell-apr"] for f in index._index.filters)
    note = _recent_note(hits)
    assert "from 2026-04-29" in note and "most recent recording that is" not in note


@pytest.mark.anyio
async def test_a_subject_the_newest_streams_never_mention_goes_to_the_last_one_that_did(monkeypatch):
    """ThreadGuy on the Anthropic S-1: the search put the last two days
    at 0.80 of the best, near enough to pass. The reranker, reading them,
    gave 0.307 against 0.688 for the stream nine days earlier."""
    shelf = [{"id": "sep-08", "published_at": "2026-09-08", "title": "sep-08"},
             {"id": "sep-29", "published_at": "2026-09-29", "title": "sep-29"},
             {"id": "oct-08", "published_at": "2026-10-08", "title": "oct-08"},
             {"id": "oct-09", "published_at": "2026-10-09", "title": "oct-09"}]
    index = index_over([("sep-08", .454, []), ("sep-29", .45, []),
                        ("oct-08", .361, []), ("oct-09", .35, [])],
                       shelf=shelf, monkeypatch=monkeypatch)
    index._settings = index._settings.model_copy(update={"rerank_model": "reads"})
    reading({"sep-08": .684, "sep-29": .688, "oct-08": .307, "oct-09": .295}, monkeypatch)
    hits = await index._retrieve("what is the latest on the anthropic s-1", 6)
    assert {h.episode_id for h in hits} == {"sep-29"}
    assert '"oct-09" (2026-10-09)' in _recent_note(hits)


@pytest.mark.anyio
async def test_the_reranker_is_read_once_and_only_for_questions_about_now(monkeypatch):
    index = record_index(ON_THE_RECORD, monkeypatch)
    seen = reading({"cz-sep": .65, "powell-apr": .66}, monkeypatch)
    await index._retrieve("what did jamie dimon say about private credit", 6)
    assert seen == []
    await index._retrieve("what did cz say on the latest interview", 6)
    assert len(seen) == 1


@pytest.mark.anyio
async def test_when_the_reranker_is_down_nothing_is_claimed(monkeypatch):
    """The newest recording is not it, and nothing can say which older
    one is. Searched across everything, with no note about recency."""
    index = record_index(ON_THE_RECORD, monkeypatch)

    async def down(*args, **kwargs):
        return None
    monkeypatch.setattr(podcast, "rerank_scores", down)
    monkeypatch.setattr(podcast, "rerank_order", down)
    hits = await index._retrieve("what did the fed chair say at the latest press conference", 6)
    assert hits[0].episode_id == "powell-apr"
    assert index._index.filters[-1] is None
    assert _recent_note(hits) is None


@pytest.mark.anyio
async def test_a_search_that_hangs_costs_the_narrowing_and_not_the_answer(monkeypatch):
    """What happened on the real index: one of the two searches that
    place a question in time hung on a dead socket and the question came
    back as an error. It is answered by the ordinary search instead."""
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    plain = index._index.query

    def hangs_when_narrowed(**kwargs):
        if "episode_id" in (kwargs.get("filter") or {}):
            raise TimeoutError("half-open socket")
        return plain(**kwargs)
    index._index.query = hangs_when_narrowed
    hits = await index._retrieve("what did ansem pick as his trades this week", 6)
    assert [h.episode_id for h in hits][:2] == ["may", "june"]
    assert _recent_note(hits) is None


@pytest.mark.anyio
async def test_the_extra_searches_are_given_less_time_than_the_ordinary_one(monkeypatch):
    index = index_over(OLD_AND_NEW, monkeypatch=monkeypatch)
    waited = []
    real = podcast.asyncio.wait_for

    async def timed(coroutine, timeout):
        waited.append(timeout)
        return await real(coroutine, timeout)
    monkeypatch.setattr(podcast.asyncio, "wait_for", timed)
    await index._retrieve("what did ansem pick as his trades this week", 6)
    assert waited and set(waited) == {PodcastIndex._NOW_WITHIN}
    waited.clear()
    await index._retrieve("what did ansem say about zcash", 6)
    assert waited == [index._settings.pinecone_read_timeout_seconds]


# --- the cache ----------------------------------------------------------------

def test_an_answer_about_this_week_is_not_kept_for_a_day(monkeypatch):
    import app.main as main

    assert main._timed("what did ansem say about zcash") == "what did ansem say about zcash"
    stamped = main._timed("what did ansem pick this week")
    assert stamped.startswith("what did ansem pick this week @20")
    source = (ROOT / "app" / "main.py").read_text()
    for surface in ("front-door", "front-door-stream", "podcast", "podcast-stream",
                    "elon-v2", "finance", "threadguy-v3", "mcg"):
        assert f'make_key(_timed(body.query), surface="{surface}"' in source, surface
    assert "make_key(_timed(query), surface=surface" in source


def test_every_archive_is_given_its_shelf_at_startup():
    source = (ROOT / "app" / "main.py").read_text()
    assert '("podcast", _podcast_shelf)' in source
    for name, load in (("elon", "_elon_episodes"), ("mcg", "_mcg_episodes"),
                       ("tradfi", "_tradfi_episodes"),
                       ("threadguy", "_threadguy_episodes")):
        assert f'("{name}", _shelf_of({load}))' in source, name
    assert "_follow(_shelf)" in source

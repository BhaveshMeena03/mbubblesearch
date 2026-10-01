"""The exact-token index, and the promise that it can only add.

Semantic search is weak on rare tokens: a name or a number is one word in
four hundred and barely moves an embedding, so the passage that literally
contains it can rank below passages merely about the same subject. Three
misses in one day came from that.

The rule this file exists to hold is that the fix is strictly additive. It
contributes candidates to the pool the reranker already scores. It must
never reorder, never drop, and never be able to turn a working answer into
a worse one — retrieval quality is the product.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.terms import TermIndex  # noqa: E402

INDEX = ROOT / "data" / "term_index.json"


def test_the_shipped_index_covers_the_queries_that_failed():
    """Each of these missed in production, and each is in the archive."""
    terms = TermIndex()
    assert len(terms) > 1000, "the index looks empty"

    for query in ("what did andre say",
                  "what did mayne n ansem talk about",
                  "who made 54 million on the drop"):
        assert terms.lookup(query), query


def test_it_stays_out_of_the_way_of_questions_that_work():
    """A query with no rare token contributes nothing, so those searches
    are byte-for-byte what they were before this existed."""
    terms = TermIndex()
    for query in ("what did they say about pump fun fees",
                  "what did ansem say about solana"):
        assert terms.lookup(query) == [], query


def test_a_missing_index_is_not_an_error(tmp_path):
    """Absent or unreadable degrades to the old behaviour, silently and
    on purpose: search must not fail because an optimisation is missing."""
    assert TermIndex(tmp_path / "nope.json").lookup("what did andre say") == []

    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert TermIndex(broken).lookup("what did andre say") == []


def test_a_bare_number_is_not_indexed_but_a_phrase_is():
    """"54" is in dozens of windows and says nothing; "54 million" is in
    a handful and is the whole question.

    The count is a band rather than an exact number. This asserted
    exactly one window and went red the day four more broadcasts were
    indexed and another episode happened to say "54 million" -- a true
    fact about a growing archive failing a test about the indexer. What
    the indexer promises is that a bare number is dropped and a phrase
    is kept and stays selective; how many windows say a given phrase is
    the corpus's business, not this file's.
    """
    raw = json.loads(INDEX.read_text())
    assert "54" not in raw["terms"], "a bare number carries no question"
    assert "54 million" in raw["terms"], "the phrase is the whole question"
    windows = raw["terms"]["54 million"]
    assert 1 <= len(windows) <= 20, (
        f"{len(windows)} windows: selective enough to be worth a lookup, "
        "and if this ever grows past the cap the phrase has stopped "
        "identifying a moment")


def test_lookups_are_capped():
    """This only needs to put a few candidates in front of the reranker.
    Flooding the pool would let a keyword match crowd out the embedding's
    own choices, which is the one thing it must not do."""
    terms = TermIndex()
    assert len(terms.lookup("kimchi", limit=3)) <= 3
    assert len(terms.lookup("kimchi")) <= 8


def test_rarest_terms_come_first():
    """A token in two windows says far more about what was meant than one
    in fifty, and only the first few survive the cap."""
    terms = TermIndex()
    raw = json.loads(INDEX.read_text())
    # "andre" is rare, "trading" is not; the rare one must lead.
    picked = terms.lookup("what did andre say about trading", limit=3)
    andre_ids = {raw["ids"][i] for i in raw["terms"]["andre"]}
    assert picked and picked[0] in andre_ids


# ---- every archive, ranked lookups -------------------------------------
# Every archive used to load Market Bubble's index, so ThreadGuy, MCG,
# Elon and The Record had no exact matching at all. Two questions missed
# on ThreadGuy because of it: "what did blurr say about bucket shops" and
# "what did threadguy say about the anthropic s-1".

import gzip  # noqa: E402

from app.podcast import PodcastIndex  # noqa: E402
from app.schemas import PodcastHit  # noqa: E402
from app.terms import path_for, words, worth_indexing  # noqa: E402


def test_hyphens_are_optional_so_s1_and_s_1_are_one_word():
    """The title said "S-1", the transcript "S1", the question "S-1"."""
    assert words("the Anthropic S-1 leaked") == ["the", "anthropic", "s1", "leaked"]
    assert words("Anthropic S1") == ["anthropic", "s1"]


def test_two_characters_count_when_they_mix_letters_and_digits():
    assert worth_indexing("s1") and worth_indexing("q3")
    assert not worth_indexing("is") and not worth_indexing("ok")
    assert not worth_indexing("10")


def test_each_archive_reads_its_own_index():
    assert path_for("podcast") == INDEX
    assert path_for("threadguy").name == "terms_threadguy.json.gz"
    for name in ("threadguy", "mcg", "elon", "tradfi"):
        assert len(TermIndex(path_for(name))) > 1000, name


def test_the_threadguy_index_holds_the_words_that_missed():
    raw = json.loads(gzip.decompress(path_for("threadguy").read_bytes()))
    assert raw["terms"].get("s1"), "said as 'S1' in the S-1 stream"
    assert raw["terms"].get("bucket"), "Blurr's 'bucket shop'"


def _index(tmp_path, terms, title_terms=None, n=10, gz=True):
    body = {"ids": [f"id{i}" for i in range(n)], "terms": terms}
    if title_terms is not None:
        body["title_terms"] = title_terms
    path = tmp_path / ("t.json.gz" if gz else "t.json")
    data = json.dumps(body).encode()
    path.write_bytes(gzip.compress(data) if gz else data)
    return TermIndex(path)


def test_a_window_with_two_of_the_words_beats_one_with_either(tmp_path):
    terms = _index(tmp_path, {"blurr": [1, 2, 3], "bucket": [3, 7]})
    assert terms.lookup("blurr bucket", limit=1) == ["id3"]


def test_saying_the_word_beats_only_having_it_in_the_title(tmp_path):
    terms = _index(tmp_path, {"s1": [6]}, {"s1": [1, 2, 3, 6]})
    picked = terms.lookup("anthropic s1", limit=2)
    assert picked[0] == "id6", "said AND under the title comes first"
    assert picked[1] == "id1", "title-only windows follow, in storage order"


def test_saying_it_more_often_ranks_higher(tmp_path):
    terms = _index(tmp_path, {"s1": [2, 5, 5, 5, 8]})
    assert terms.lookup("s1", limit=1) == ["id5"]


def test_an_index_from_before_title_terms_still_works(tmp_path):
    terms = _index(tmp_path, {"andre": [4]}, gz=False)
    assert terms.lookup("what did andre say") == ["id4"]


def _hit(start, text, episode="e"):
    return PodcastHit(episode_id=episode, title="Anthropic S-1 is UHHH",
                      start_seconds=start, timestamp="0:00", deep_link="x",
                      text=text, score=0.0)


def test_passages_that_say_the_word_are_added_never_swapped_in(tmp_path):
    """The reranker scored 'Malcolm, you think I wasn't going to notice?'
    above the minute reading the leaked S-1 out, because both sit under
    the title 'Anthropic S-1 is UHHH'. The minute that says it is kept;
    nothing the reranker chose is removed or reordered."""
    index = PodcastIndex.__new__(PodcastIndex)
    index._terms = _index(tmp_path, {"s1": [0, 1, 2]})
    chosen = [_hit(10, "Malcolm, you think I wasn't going to notice?")]
    exact = [_hit(20, "S1 once"), _hit(30, "the S1 leaked, the S1 numbers, S1"),
             _hit(40, "nothing about it"), _hit(10, "already chosen S1")]
    added = index._said_it("what did he say about the anthropic s-1", exact, chosen)
    assert [h.start_seconds for h in added] == [30, 20], \
        "most mentions first, only ones that say it, never a repeat"
    assert len(added) <= PodcastIndex._SAID_IT_SLOTS


def test_nothing_is_added_when_the_question_has_no_rare_word(tmp_path):
    index = PodcastIndex.__new__(PodcastIndex)
    index._terms = _index(tmp_path, {"s1": [0]})
    assert index._said_it("what did he say", [_hit(20, "S1")], []) == []

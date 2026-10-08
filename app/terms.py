"""Exact-token lookup, for the part of a query an embedding cannot carry.

A rare token is one word in four hundred. It barely moves the vector, so a
passage that literally contains it can rank below passages that are only
about the same subject. Three misses in one day came from that, every one
of them a thing the archive holds:

    "what did andre say"                -> Andrew Tate came back, while
                                           Andre from Grass sat in the index
    "what did mayne n ansem talk about" -> missed, though "what did mayne
                                           say" answers from his own episode
    "who made 54 million on the drop"   -> missed a line reading
                                           "54 million dollars on the drop"

What this does NOT do is decide anything. It contributes candidates to the
pool the reranker already scores, and the reranker still chooses the order
and the cut. A passage matched here that is not actually relevant is one
more thing for the reranker to reject, which it is better at than a
keyword rule would ever be. That is deliberate: retrieval quality is the
product, and this must only ever be able to add a right answer, never
remove one.

The index is built by scripts/build_term_index.py and shipped in the
image. It holds vector ids, not text, so it stays under a megabyte and
cannot drift from what the vectors say.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import re
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX = Path(__file__).resolve().parent.parent / "data" / "term_index.json"


def path_for(namespace: str) -> Path:
    """Each archive's own index. Market Bubble's keeps its original name.

    Every archive used to load Market Bubble's, so a ThreadGuy search
    looked up broadcast ids and asked Pinecone for them in a namespace
    where they cannot exist: a wasted fetch on every query, and no exact
    matching for any other archive at all.
    """
    if namespace == "podcast":
        return INDEX
    # Gzipped: built from Pinecone and rebuilt as the archive grows, at
    # three to four megabytes of JSON each, a fifth of that compressed.
    return INDEX.with_name(f"terms_{namespace}.json.gz")


def read(path: Path) -> dict:
    """An index file, plain or gzipped by its name."""
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return json.loads(data)

# Same shape the builder uses, so a query is tokenised the way the corpus
# was. Any divergence here silently stops matches from ever being found.
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'.\-]{1,}")

# Words that would match half the corpus. The builder drops these by
# document frequency; this is the query side of the same idea, and it is
# short on purpose — the df cap does the real work.
_SKIP = frozenset("""
what which who whom whose when where why how did does do say says said talk
talked about discuss discussed think thinks mention mentioned the a an and or
but for with from that this they them their there here was were are is
""".split())

def words(text: str) -> list[str]:
    """Words as both the index and the query see them.

    The builder imports this, so a query is tokenised exactly the way the
    corpus was; two copies of these rules would drift and silently stop
    matches. Lowercased, end punctuation trimmed (so "Pump.fun." and
    "Pump.fun" are one word, the inner dot being part of the name), and
    hyphens dropped: the S-1 stream's title says "S-1", what was said
    transcribes as "S1", and the question was asked as "S-1".
    """
    return [w.strip(".-'").replace("-", "") for w in _TOKEN.findall(text.lower())]


def said_phrases(said: list[str]) -> list[str]:
    """The known caption manglings of more than one word in these words.

    Shared with the builder for the same reason words() is: a phrase the
    index wrote one way and the query read another is a match that never
    happens, silently. `said` is the output of words(), and the phrases go
    through it too, so "Z cash" is just "cash" on both sides and is not a
    phrase at all.
    """
    from app import names

    joined = f" {' '.join(said)} "
    found = []
    for phrase in names.phrases():
        normal = " ".join(words(phrase))
        if " " in normal and f" {normal} " in joined:
            found.append(normal)
    return list(dict.fromkeys(found))


def worth_indexing(word: str) -> bool:
    """Three characters or more, or two that mix letters and digits.

    The length floor keeps out "is" and "ok". It also kept out "s1", "q3"
    and "h2", which are exactly the rare, specific tokens this exists for.
    """
    if len(word) >= 3:
        return True
    return (len(word) == 2 and any(c.isalpha() for c in word)
            and any(c.isdigit() for c in word))


# What a token in a window's episode title adds, against one found in what
# was said. Enough that "what did mayne say" still lands in Mayne's
# episode, too little for the title alone to outrank a minute that says
# the word.
_IN_TITLE = 0.4


class TermIndex:
    """Vector ids for the rare tokens in a query.

    Loaded once. Missing or unreadable means every lookup returns nothing,
    which degrades to exactly the behaviour before this existed.
    """

    def __init__(self, path: Path = INDEX) -> None:
        self._ids: list[str] = []
        self._terms: dict[str, list[int]] = {}
        # Windows whose episode TITLE holds the token, whether or not it
        # was said in them. The title is embedded with every window, so a
        # guest's name
        # in a title used to post every window of that episode, and a
        # lookup took the first eight of them in storage order: "what did
        # blurr say about bucket shops" got eight arbitrary minutes of the
        # Papertrade episode and never the one that says "bucket shop".
        # Absent in an older index, which then behaves as it always did.
        self._title: dict[str, list[int]] = {}
        try:
            raw = read(path)
            self._ids = raw.get("ids") or []
            self._terms = raw.get("terms") or {}
            self._title = raw.get("title_terms") or {}
        except FileNotFoundError:
            logger.info("no term index at %s — exact matching is off", path)
        except (OSError, EOFError, ValueError) as exc:
            logger.warning("term index unreadable (%s) — exact matching is "
                           "off, semantic search is unaffected", exc)

    def __len__(self) -> int:
        return len(self._terms.keys() | self._title.keys())

    def _known(self, term: str) -> bool:
        return term in self._terms or term in self._title

    def _df(self, term: str) -> int:
        return len(set(self._terms.get(term, ())) | set(self._title.get(term, ())))

    def terms_in(self, query: str) -> list[str]:
        """The query's tokens this index knows, as the lookup reads them."""
        return self._query_terms(query) if (self._terms or self._title) else []

    def _query_terms(self, query: str) -> list[str]:
        said = words(query)
        found = [w for w in said
                 if worth_indexing(w) and w not in _SKIP and self._known(w)]
        # Numeric phrases: "54 million" is in one window, "54" is in dozens,
        # which is why the bare number is not in the index at all.
        for first, second in zip(said, said[1:], strict=False):
            if first and first[0].isdigit():
                phrase = f"{first} {second}"
                if self._known(phrase):
                    found.append(phrase)
        # And a mangling of more than one word, which is rare where each
        # of its words is not: the window that says it then outranks the
        # ones that only say half of it.
        found += [p for p in said_phrases(said) if self._known(p)]
        return list(dict.fromkeys(found))

    def lookup(self, query: str, limit: int = 8) -> list[str]:
        """Vector ids for passages containing a rare token from the query.

        Ranked, then capped: this only needs to put a few candidates in
        front of the reranker, not flood it. A window scores the rarity of
        every query token it holds, so one holding two of them ("blurr" and
        "bucket") beats one holding either. A token that is only in the
        window's episode title counts for less than one that was said in
        it, because the title is on every window of that episode and says
        nothing about which minute; a window with both gets both. Ties
        keep storage order.
        """
        if not (self._terms or self._title):
            return []
        terms = self._query_terms(query)
        if not terms:
            return []
        total = len(self._ids)
        score: dict[int, float] = defaultdict(float)
        for term in terms:
            # Rarer tokens weigh more: one in two windows says far more
            # about what was meant than one in fifty.
            weight = math.log((total + 1) / (self._df(term) + 1)) + 1.0
            for position in self._terms.get(term, ()):
                score[position] += weight
            for position in self._title.get(term, ()):
                score[position] += weight * _IN_TITLE
        ranked = sorted((p for p in score if p < total),
                        key=lambda p: (-score[p], p))
        logger.debug("term index matched %s", terms[:3])
        return [self._ids[p] for p in ranked[:limit]]

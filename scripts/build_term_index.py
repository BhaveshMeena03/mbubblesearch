"""Build the exact-token index that semantic search cannot replace.

    .venv/bin/python scripts/build_term_index.py
    .venv/bin/python scripts/build_term_index.py --max-df 60
    .venv/bin/python scripts/build_term_index.py --archive threadguy
    .venv/bin/python scripts/build_term_index.py --archive mcg   (also elon, tradfi)

Embeddings carry meaning, and a rare token carries almost none. Three
misses in one day came from that, all of them things the archive holds:

    "what did andre say"              -> returned Andrew Tate, while Andre
                                         from Grass sat in the index
    "what did mayne n ansem talk about" -> missed, though "what did mayne
                                         say" answers from his own episode
    "who made 54 million on the drop" -> missed a line that says
                                         "54 million dollars on the drop"

A name or a number is one token out of four hundred in a window. It moves
the vector barely at all, so the passage that literally contains it can
rank below passages that are merely on the same subject.

This maps those tokens to the vectors that contain them, so a query
carrying one can pull the exact passage into the candidate pool. It does
not reorder anything — the reranker still decides. See _retrieve.

Only RARE tokens are kept. A token in half the corpus tells you nothing
and would cost more to store than it could ever be worth. The ids are
recomputed here exactly as ingest computes them, from the same windowing,
so no Pinecone read is needed to build this and the ids stay valid as
long as the chunking does.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.podcast import _windows  # noqa: E402
from app.schemas import Episode  # noqa: E402
from app.terms import path_for, words, worth_indexing  # noqa: E402

EPISODES = ROOT / "data" / "episodes.json"

# How many times one window may be counted for one token. Enough to tell a
# passing mention from the minute about it, not so many that one rambling
# minute outranks every other.
MAX_REPEATS = 4

# Ids per Pinecone fetch. The fetch is a GET with every id in the query
# string, so a few hundred at a time keeps the URL well short of any limit.
FETCH_BATCH = 200

# Words that appear everywhere and discriminate nothing. Deliberately short:
# the document-frequency cap does most of the work, and a long hand-written
# stoplist is how you accidentally drop a name.
STOP = set("""
the a an and or but if then than that this these those there here of to in on
at for with from by as is are was were be been being am do does did doing
have has had having will would shall should can could may might must not no
nor so too very just also only even still yet about into over under again
more most other some any all each both few own same such only own it its it's
i you he she we they me him her us them my your his our their mine yours
what which who whom whose when where why how
like get got go going gonna want know think thing things really actually
right yeah yes okay ok well now then time way lot lots make makes made
say says said see seen look looking come came take took give gave
one two three four five six seven eight nine ten
""".split())

def phrases(text: str) -> set[str]:
    """Two-word phrases starting with a number.

    A bare number is useless: "54" is in more windows than the cap allows,
    because every transcript is full of numbers. "54 million" is in one.
    That is the difference between finding "who made 54 million on the
    drop" and missing a line that says exactly that.
    """
    said = words(text)
    out = set()
    for first, second in zip(said, said[1:], strict=False):
        if first and first[0].isdigit() and len(second) > 2:
            out.add(f"{first} {second}")
    return out


def tokens(text: str) -> set[str]:
    """Content tokens, lowercased, punctuation trimmed.

    Numbers are kept and matter: "54 million on the drop" is the case this
    exists for. A trailing full stop is stripped so "Pump.fun." and
    "Pump.fun" are one token, while the internal dot survives because it is
    part of the name.
    """
    return {w for w in words(text) if worth_indexing(w) and w not in STOP}


def from_episodes(settings) -> tuple[list[str], list[tuple[str, str]]]:
    """Market Bubble: windows rebuilt from the transcripts on disk.

    The ids are recomputed exactly as ingest computes them, from the same
    windowing, so no Pinecone read is needed.
    """
    episodes = [Episode(**e) for e in json.loads(EPISODES.read_text())]
    ids, docs = [], []
    for episode in episodes:
        for start_t, text, _stamped in _windows(
            episode.segments, settings.chunk_max_chars, overlap_segments=2
        ):
            ids.append(hashlib.sha256(
                f"{episode.episode_id}:{start_t}".encode()
            ).hexdigest()[:32])
            docs.append((episode.title, text))
    return ids, docs


def from_pinecone(namespace: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Any archive whose transcripts live only in Pinecone.

    ThreadGuy and MCG keep no transcripts on disk by design: the shelf holds
    titles and runtimes, the vectors hold the text. So the index is read
    off the vectors themselves, every id in the namespace and the text
    stored with it, which also means it can never disagree with them.
    """
    from app.podcast import PodcastIndex
    # MCG lives in its own Pinecone index; every other archive shares one.
    settings = get_settings()
    name = settings.mcg_pinecone_index if namespace == settings.mcg_namespace else None
    index = PodcastIndex(namespace=namespace, index_name=name).index
    # Newer clients yield items carrying .id; older ones yield the id.
    every = [getattr(item, "id", item)
             for page in index.list(namespace=namespace) for item in page]
    ids, docs = [], []
    for at in range(0, len(every), FETCH_BATCH):
        batch = every[at:at + FETCH_BATCH]
        records = index.fetch(ids=batch, namespace=namespace).vectors or {}
        for vector_id in batch:
            record = records.get(vector_id)
            md = getattr(record, "metadata", None) or {}
            if md.get("text"):
                ids.append(vector_id)
                docs.append((md.get("title", ""), md["text"]))
        print(f"  read {min(at + FETCH_BATCH, len(every)):,} of {len(every):,}",
              end="\r", flush=True)
    print()
    return ids, docs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-name-df", type=int, default=250,
                    help="ceiling for a name from an episode title; higher "
                         "than --max-df because a guest is said often "
                         "inside their own episode")
    ap.add_argument("--max-df", type=int, default=60,
                    help="drop a token appearing in more windows than this; "
                         "common words cost storage and tell you nothing")
    ap.add_argument("--archive", default="podcast",
                    help="podcast reads data/episodes.json; any other "
                         "namespace is read from Pinecone")
    args = ap.parse_args()

    out = path_for(args.archive)
    ids, docs = (from_episodes(get_settings()) if args.archive == "podcast"
                 else from_pinecone(args.archive))

    # Two maps: where a token was SAID, and where it is only in the
    # episode title. The title is embedded with every window at ingest, so
    # a name only in a title is still findable, but it is on every window
    # of that episode and says nothing about which minute. Kept apart so
    # the lookup can rank a window that says the word above one that is
    # merely in the right episode.
    said_in: dict[str, list[int]] = defaultdict(list)
    titled: dict[str, list[int]] = defaultdict(list)
    for position, (title, text) in enumerate(docs):
        # Once per time it was said, up to MAX_REPEATS. Twenty-seven minutes
        # of the S-1 stream say "S1"; the one reading the filing's numbers
        # out says it five times, and a lookup with no way to tell them
        # apart took the first eight in storage order.
        counts = Counter(w for w in words(text)
                         if worth_indexing(w) and w not in STOP)
        counts.update(phrases(text))
        for token, n in counts.items():
            said_in[token].extend([position] * min(n, MAX_REPEATS))
        # Every window of the episode, said or not: a minute that says
        # "S1" inside the stream titled "Anthropic S-1" is a better match
        # than one saying it anywhere else, and gets both weights.
        for token in tokens(title) | phrases(title):
            titled[token].append(position)
    # Document frequency counts a window once, whichever map it is in.
    postings = {t: sorted(set(said_in.get(t, [])) | set(titled.get(t, [])))
                for t in said_in.keys() | titled.keys()}

    # Names are exempt from the frequency cap.
    #
    # The cap exists to drop words that tell you nothing — "about", "think",
    # "market". It was dropping names too, and got worse as the archive
    # grew: ingesting the full Greg Osuri & Mayne broadcast pushed "mayne"
    # past sixty windows, so the episode about Mayne is what made "mayne"
    # unsearchable by name. Raising the cap instead let "pump fun fees"
    # match, which is the opposite failure — exact match should only ever
    # fire on something rare.
    #
    # A name from an episode title is precisely what people type, however
    # often it is said inside the episode. Those are kept whatever their
    # frequency; everything else still faces the cap.
    # A token from a title is only a NAME if it is specific to an episode
    # or two. "ansem", "market", "bubble", "solana" appear in most titles —
    # exempting those let "what did ansem say about solana" match on exact
    # tokens, which is the noise this cap exists to prevent. A guest is in
    # one title, so that is the test.
    title_df: dict[str, int] = defaultdict(int)
    for title in {title for title, _ in docs}:
        for token in tokens(title) | phrases(title):
            title_df[token] += 1
    names = {t for t, n in title_df.items() if n <= 2}

    # Names get a higher ceiling, not an unlimited one. Measured across this
    # archive the two groups separate cleanly: guests sit at 104-193 windows
    # (clemente 104, mayne 105, orangie 165, mizkif 188, tjr 193) and topics
    # at 352+ (solana 352, bitcoin 446). Without the second cap "solana"
    # counted as a name — it is in one title — and "what did ansem say about
    # solana" started matching on exact tokens, which is the noise this
    # whole filter exists to keep out.
    kept = {t: p for t, p in postings.items()
            if len(p) <= args.max_df
            or (t in names and len(p) <= args.max_name_df)}
    dropped = len(postings) - len(kept)
    rescued = sum(1 for t in kept if len(kept[t]) > args.max_df)

    body = json.dumps(
        {"ids": ids,
         "terms": {t: said_in[t] for t in kept if said_in.get(t)},
         "title_terms": {t: titled[t] for t in kept if titled.get(t)}},
        separators=(",", ":")).encode()
    # mtime=0 so an unchanged index writes identical bytes and git sees
    # no change.
    out.write_bytes(gzip.compress(body, mtime=0) if out.suffix == ".gz" else body)
    size = out.stat().st_size

    print(f"  {len(ids):,} windows")
    print(f"  {len(postings):,} distinct tokens, {len(kept):,} kept "
          f"({dropped:,} too common at df>{args.max_df}, "
          f"{rescued:,} kept anyway as names)")
    print(f"  {out.relative_to(ROOT)} — {size/1_000_000:.2f} MB")
    if size > 4_000_000:
        print("\n  That is large for the image. Lower --max-df.")
    # The cases this was built for.
    for probe in ("andre", "mayne", "kimchi", "pollock",
                  "54 million", "1.37 million", "500 dollars"):
        print(f"    {probe:8} -> {len(kept.get(probe, [])):3} windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

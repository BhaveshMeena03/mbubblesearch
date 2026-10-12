"""Questions about now.

"What did Ansem pick as his trades this week" was asked two days after a
show in which he named three, and the archive answered from May and June.
Nothing in a search by meaning knows when anything was said: "this week"
is two words in a question whose other words match a dozen older shows
just as well, and the newest hour of tape is one episode in fifty.

So a question that is about the present is searched in the present. This
says whether a question is one of those and how far back it reaches, and
picks the recordings that fall inside that reach. The index does the rest
with a filter on the episode, the way it already narrows to passages
where a named host speaks: same vectors, same search, a smaller place to
look.

"Now" is the newest recording in the archive, not the clock on the wall.
An archive whose last upload was nine days ago still has a latest
episode, and somebody asking for it does not want to be told nothing
aired this week.

A phrase is here only if it points at the present on its own. "Last" is
not: "last cycle" and "last year" are about the past, and people say them
constantly.

The newest recordings are only the first place to look. The Record is ten
people and the newest thing on its shelf is an interview with CZ, so the
first version of this answered "what did the Fed chair say at the latest
press conference" from CZ. Two things follow. When a question names one of
the people an archive is filed under, "now" is that person's newest
recording. And when the newest recordings turn out not to be about the
question, it is answered from the most recent recording that is, and the
model is told it is not the newest, so it says the date instead of calling
it current.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# The newest recording, and the day before it: a Market Bubble show goes
# out on X on Thursday and its YouTube cut is dated Friday, and both are
# "the latest episode".
_LATEST = 2
_SHOW = r"(?:episode|ep|show|stream|broadcast|pod|podcast|market\s+open|one)"
_WINDOWS: tuple[tuple[re.Pattern, int], ...] = (
    (re.compile(rf"""(?ix)\b(?:
          latest | most\s+recent
        | (?:last|newest|previous) \s+ {_SHOW}
        | last\s+night(?:'?s)? | tonight(?:'?s)?
        | today(?:'?s)? | yesterday(?:'?s)?
        )\b"""), _LATEST),
    (re.compile(r"""(?ix)\b(?:
          this\s+(?:past\s+)?week(?:'?s)? | (?:the\s+)?past\s+week
        | recently | lately | these\s+days | nowadays
        | right\s+now | currently | at\s+the\s+moment
        )\b"""), 7),
    (re.compile(r"(?i)\blast\s+week(?:'?s)?\b"), 14),
    (re.compile(r"(?i)\b(?:this|(?:the\s+)?past)\s+month(?:'?s)?\b"), 31),
    (re.compile(r"(?i)\blast\s+month(?:'?s)?\b"), 62),
)


def window_days(query: str) -> int | None:
    """How many days back from the newest recording a question reaches.

    None when the question is not about now. The tightest phrase wins:
    "the latest on what he said this month" is asking for the latest.
    """
    found = [days for pattern, days in _WINDOWS if pattern.search(query or "")]
    return min(found) if found else None


@dataclass(frozen=True)
class Recent:
    """The recordings a question about now should be answered from."""

    ids: tuple[str, ...]
    since: str          # the oldest date among them
    newest: str         # the newest date among them
    title: str          # of the newest of them
    # Set when these are not the newest recordings in the archive: the
    # title and date of the one that is, which had nothing on the question.
    after: tuple[str, str] | None = None

    def note(self) -> str:
        """What the model is told, after the excerpts and the question.

        It cannot see what was left out, so it is told: without this it
        would treat six passages from one week as six passages chosen
        from two years and hedge accordingly.
        """
        span = (f"from {self.since} to {self.newest}"
                if self.since != self.newest else f"from {self.newest}")
        if self.after:
            title, day = self.after
            return (
                "This question is about what is recent. The newest recording "
                f"searched is \"{title}\" ({day}), and nothing in the newest "
                "recordings is close to what was asked. The excerpts "
                "above are from the most recent recording that is: "
                f"\"{self.title}\" ({self.newest}). Answer from them, give "
                "that date, and say plainly that this is the most recent the "
                "archive has on it. Do not describe it as this week's or as "
                "the newest recording, and do not answer from memory.")
        return (
            "This question is about what is recent. The excerpts above were "
            f"taken only from the newest recordings in this archive, {span}; "
            f"the newest is \"{self.title}\" ({self.newest}). Answer from "
            "them and say which recording and date each point comes from. "
            "If they do not cover what was asked, say that the most recent "
            "recordings do not, rather than answering from memory or "
            "implying an older view is current.")


def _day(row: dict) -> str:
    return str(row.get("published_at") or "")[:10]


def _dated(rows: list[dict]) -> list[tuple[str, str, str]]:
    """(date, id, title) for every row that can be placed in time, oldest
    first. A row with no date cannot be, and is left out."""
    dated = []
    for row in rows or ():
        ident = str(row.get("episode_id") or row.get("id") or "")
        stamp = _day(row)
        try:
            date.fromisoformat(stamp)
        except ValueError:
            continue
        if ident:
            dated.append((stamp, ident, str(row.get("title") or "")))
    dated.sort()
    return dated


def recent(rows: list[dict], days: int, *, upto: str | None = None) -> Recent | None:
    """The recordings within `days` of the newest one in `rows`.

    Rows are whatever shelf the archive keeps: an id under "episode_id" or
    "id", a "published_at" that starts with a date, a title.

    `upto` ends the window on an earlier day instead: the same reach, back
    from the last recording that had something to say.
    """
    dated = _dated(rows)
    if upto:
        dated = [row for row in dated if row[0] <= upto]
    if not dated:
        return None
    newest, _, title = dated[-1]
    floor = (date.fromisoformat(newest) - timedelta(days=days - 1)).isoformat()
    inside = [(stamp, ident) for stamp, ident, _ in dated if stamp >= floor]
    # An id can sit on a shelf twice; order kept, newest last.
    ids = tuple(dict.fromkeys(ident for _, ident in inside))
    return Recent(ids=ids, since=inside[0][0], newest=newest, title=title)


def named(rows: list[dict], query: str) -> list[dict]:
    """The recordings of the person a question names, or all of them.

    Only a shelf that files each recording under a person has anything to
    match, by a "subject" on the row. "What has Powell said recently" is
    then about Powell's newest recording and not the archive's, which
    belongs to somebody else. Matched on the whole name or the surname, as
    a word: "powell's" yes, "powellton" no.
    """
    asked = (query or "").lower()
    mine = []
    for row in rows or ():
        subject = str(row.get("subject") or "").strip().lower()
        if not subject or subject == "unknown":
            continue
        surname = subject.split()[-1]
        if (re.search(rf"\b{re.escape(subject)}\b", asked)
                or re.search(rf"\b{re.escape(surname)}\b", asked)):
            mine.append(row)
    return mine or list(rows or ())


# Whether the newest recordings are where the answer is, from how their
# best passage scores against the best in the whole archive. Measured on
# twenty-one questions across the five archives, 12 October 2026.
#
# By the search itself: every question the newest recordings did answer
# kept at least 0.80 of the archive's best score ("which projects were on
# mcg this week", the lowest). CZ's interview, asked about the Fed chair's
# press conference, kept 0.71; asked about Warsh, Powell and Dimon, 0.51 to
# 0.64. A running show is always roughly on its own subject. A shelf of
# separate people is not.
NEAR = 0.75
# By the reranker, which reads the words: the questions the newest
# recordings answered kept 0.67 or more of the best ("what is ansem bullish
# on right now", the lowest). Asked for the latest on something they never
# mention, they kept 0.45 (ThreadGuy on the Anthropic S-1, last discussed
# nine days earlier) to 0.54 (MCG on ClawPump).
COVERS = 0.57
# And how near the best an older recording has to come to be the one the
# question is answered from instead. The present gets the benefit of the
# doubt, because it was asked for. Anything else has to be on the subject.
CLOSE = 0.85


def is_here(window_best: float, archive_best: float,
            window_read: float | None, archive_read: float | None) -> bool:
    """True when the newest recordings are about the question.

    The first pair are the search's scores, the second the reranker's,
    which may be missing: it is a quality step and allowed to fail. Both
    have to agree that the present is in the running.
    """
    if window_best <= 0 or archive_best <= 0:
        return False
    if window_best < NEAR * archive_best:
        return False
    if window_read is None or archive_read is None or archive_read <= 0:
        return True
    return window_read >= COVERS * archive_read


def last_on(read: list[tuple[str, float]], rows: list[dict], days: int,
            passed: Recent) -> Recent | None:
    """The most recent recordings that are about the question.

    `read` is (recording id, reranker's score) for the best passages in
    the whole archive. A recording counts when one of its passages comes
    within CLOSE of the best there is. The newest of those ends the
    window, which reaches as far back as the question did.

    None when nothing can be placed in time. If the newest that counts
    turns out to be inside the window that was passed over, that window
    comes back as it was: it is the present after all.
    """
    if not read:
        return None
    best = max(score for _, score in read)
    if best <= 0:
        return None
    about = {ident for ident, score in read if score >= CLOSE * best}
    days_of = {ident: stamp for stamp, ident, _ in _dated(rows)}
    placed = sorted(days_of[ident] for ident in about if ident in days_of)
    if not placed:
        return None
    if set(passed.ids) & about:
        return passed
    found = recent(rows, days, upto=placed[-1])
    if found is None:
        return None
    newest = _dated(rows)[-1]
    return Recent(ids=found.ids, since=found.since, newest=found.newest,
                  title=found.title, after=(newest[2], newest[0]))

"""Calls made on air, and what the market did afterwards.

A search answers "what did Ansem say about Starkware". This keeps the
other half: he named it on 8 October as his highest conviction spot for
the week, here is the second he said it, here is what it cost that night
and here is what it costs now.

Everything on the board has to survive four checks, and every one of them
can only remove a row.

  the words   A model reads the transcript and proposes calls. Its quote
              must be in the transcript, word for word, on the lines it
              pointed at. What is shown is then cut from the transcript
              itself, so not one word on the page came from the model.
  the voice   The line the quote starts on has to carry that host's voice
              label, and no line it runs across may carry the other's.
              Guests have no labels, so guests are not on the board.
  the asset   A ticker in a caption is not an identity. An asset is priced
              only if it is in data/call_assets.json, which is filled in
              by hand with the exact listing.
  a person    Nothing ships unless data/calls_verdicts.json says keep.
              The default is no.

The first two are in this file and are pure: no network, no model. That is
what lets a test hold every shipped row against the transcript it claims
to quote.

Losing calls stay on the board. A record of the good ones is an advert.

What does not belong on it is a trade. The first board had Ansem "short
Solana, down 64%" from a sentence in which he said he had hedged his spot
bags with shorts on the majors, twelve minutes before saying he was
bullish on Solana long term. A hedge is meant to lose when the holdings
win, and a perp position taken off the chart is closed within days. Held
against a price four months later, either one says something he never
said. So the board is for views a host would be expected to stand behind:
what he holds, what he would buy, where he thinks it goes. Hedges, setups
with a stop and a target, and ideas for the next thirty days are left off,
whichever way they went.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

HOSTS = ("Ansem", "FaZe Banks")
STANCES = ("long", "short")
# holding  says he owns it, is long or short it, bought it, is buying it
# pick     names it as the thing to buy, his pick, his highest conviction
# target   puts a price or a market cap on it
# exit     says he sold it or closed it
KINDS = ("holding", "pick", "target", "exit")

# A quote shorter than this is a fragment: "i'm long it" proves nothing
# about which asset. Longer than the ceiling is a paragraph, not a quote.
MIN_WORDS, MAX_WORDS = 6, 60
# How far from the line the model pointed at its quote may start, and how
# many lines it may run across. Models miscount lines by one or two.
SLACK, REACH = 3, 8

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?", re.I)

# Words that mark a trade and not a view, with the reason a row is refused.
# "head short" is how the captions hear "hedge short".
_TACTICAL = (
    (re.compile(r"\bhedg(?:e|ed|es|ing)\b|\bhead(?:ge)? short\b", re.I),
     "a hedge is not a call"),
    (re.compile(r"\bshort[- ]term trade\b|\bscalp(?:ed|ing)?\b|\bday trad", re.I),
     "a short-term trade is not a call"),
    (re.compile(r"\bstop (?:beneath|below|under|at|is|would)\b|\bmy stop\b"
                r"|\bstop[- ]?(?:loss|out)\b", re.I),
     "a setup with a stop is a trade, not a call"),
)


def tactical(quote: str) -> str | None:
    """Why a quote is a trade and not a view, or None.

    Reads only the words, so it catches the ones that say what they are.
    The rest is the job of whoever reads the candidates, and a short of
    any kind needs their reason on file before it is built.
    """
    for pattern, why in _TACTICAL:
        if pattern.search(quote or ""):
            return why
    return None


def _spoken(text: str) -> list[tuple[str, int, int]]:
    """Each word of a line with where it starts and ends in it."""
    # One character for one character, so the positions still hold in the
    # line as it was written.
    plain = (text or "").replace("\u2019", "'")
    return [(m.group().lower(), m.start(), m.end()) for m in _WORD.finditer(plain)]


def words(text: str) -> list[str]:
    """The words of a line, as they are compared: lower case, no
    punctuation. Captions carry none worth trusting."""
    return [word for word, _, _ in _spoken(text)]


@dataclass(frozen=True)
class Span:
    """Where a quote sits: the lines it runs across, and the characters it
    starts at in the first and ends at in the last."""

    first: int
    last: int
    start: int
    end: int


def locate(quote: str, segments: list[dict], near: int) -> Span | None:
    """Where a quote is in the transcript, or None if it is not there
    word for word.

    Looked for around the line the model named. Every word has to be
    present, in order, with nothing skipped: a quote with the "um" taken
    out is not what was said, and is refused rather than repaired.
    """
    wanted = words(quote)
    if not (MIN_WORDS <= len(wanted) <= MAX_WORDS):
        return None
    if not 0 <= near < len(segments):
        return None
    low = max(0, near - SLACK)
    high = min(len(segments), near + SLACK + REACH)
    spoken = [(word, index, start, end)
              for index in range(low, high)
              for word, start, end in _spoken(str(segments[index].get("text", "")))]
    plain = [row[0] for row in spoken]
    best: Span | None = None
    for at in range(len(plain) - len(wanted) + 1):
        if plain[at:at + len(wanted)] != wanted:
            continue
        head, tail = spoken[at], spoken[at + len(wanted) - 1]
        if tail[1] - head[1] >= REACH:
            continue
        # The one nearest where the model pointed, if it is said twice.
        if best is None or abs(head[1] - near) < abs(best.first - near):
            best = Span(first=head[1], last=tail[1], start=head[2], end=tail[3])
    return best


def voice(labels: dict[str, str], first: int, last: int) -> str | None:
    """Whose voice a run of lines is in, or None when that is not known.

    The first line has to be labelled: that is where the quote starts and
    where the link opens. Every other labelled line has to agree. A run
    that passes from one host to the other is two people, and a sentence
    two people said is nobody's call.
    """
    who = labels.get(str(first))
    if who not in HOSTS:
        return None
    for index in range(first, last + 1):
        other = labels.get(str(index))
        if other and other != who:
            return None
    return who


def cut(segments: list[dict], span: Span) -> str:
    """The transcript's own words for a quote: its spelling, its capitals,
    its punctuation, from the first word of the quote to the last."""
    lines = [str(segments[i].get("text", "")) for i in range(span.first, span.last + 1)]
    lines[-1] = lines[-1][:span.end]
    lines[0] = lines[0][span.start:]
    return " ".join(" ".join(lines).split())


@dataclass(frozen=True)
class Call:
    """One host, one asset, one moment."""

    id: str               # "<episode>:<line>:<ticker>", stable across runs
    episode_id: str
    line: int             # where the quote starts
    last_line: int
    start_seconds: float
    speaker: str
    asset: str            # as the model named it; the listing is decided later
    symbol: str
    asset_class: str
    stance: str
    kind: str
    quote: str            # cut from the transcript
    target: str           # "100 billion market cap", or ""

    def as_row(self) -> dict:
        return asdict(self)


def _symbol(value: object) -> str:
    return re.sub(r"[^A-Z0-9.]", "", str(value or "").upper())[:12]


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:24]


def accept(raw: dict, episode: dict, labels: dict[str, str]) -> tuple[Call | None, str]:
    """A model's claim, held against the transcript.

    Returns the call, or None and the reason it was refused. The reason is
    kept: a refusal rate that climbs says the prompt or the captions
    changed, and that is worth being able to see.
    """
    segments = episode.get("segments") or []
    try:
        near = int(raw.get("line"))
    except (TypeError, ValueError):
        return None, "no line"
    stance = str(raw.get("stance") or "").lower()
    kind = str(raw.get("kind") or "").lower()
    if stance not in STANCES:
        return None, "no stance"
    if kind not in KINDS:
        return None, "no kind"
    symbol = _symbol(raw.get("symbol"))
    asset = " ".join(str(raw.get("asset") or "").split())
    if not symbol and not asset:
        return None, "no asset"
    span = locate(str(raw.get("quote") or ""), segments, near)
    if span is None:
        return None, "quote is not in the transcript"
    first, last = span.first, span.last
    trade = tactical(cut(segments, span))
    if trade:
        return None, trade
    who = voice(labels, first, last)
    if who is None:
        return None, "voice not known"
    claimed = str(raw.get("speaker") or "")
    if claimed and claimed != who:
        # The model read the labels too. If it still named somebody else
        # it was reasoning from the conversation, and so is unsure.
        return None, "voice is not who the model named"
    return Call(
        # Two assets can be named in one breath, on one line.
        id=f"{episode['episode_id']}:{first}:{symbol or _slug(asset)}",
        episode_id=str(episode["episode_id"]),
        line=first, last_line=last,
        start_seconds=float(segments[first].get("t", 0.0)),
        speaker=who, asset=asset, symbol=symbol,
        asset_class=str(raw.get("asset_class") or "crypto").lower(),
        stance=stance, kind=kind,
        quote=cut(segments, span),
        target=" ".join(str(raw.get("target") or "").split()),
    ), ""


def one_per_show(found: list[Call]) -> list[Call]:
    """A host on one asset in one show, once.

    He says he is long the same coin four times in an hour. That is one
    call. The first is kept, because that is when it was said, unless a
    later one is a pick or a target and the first was only a holding: the
    stronger statement is the one worth a row.
    """
    rank = {"target": 0, "pick": 1, "holding": 2, "exit": 2}
    kept: dict[tuple, Call] = {}
    for call in sorted(found, key=lambda c: (c.episode_id, c.line)):
        key = (call.episode_id, call.speaker, call.symbol or call.asset.lower(),
               call.stance if call.kind != "exit" else "exit")
        held = kept.get(key)
        if held is None or rank[call.kind] < rank[held.kind]:
            kept[key] = call
    return sorted(kept.values(), key=lambda c: (c.episode_id, c.line))


# --- when it was said, and what it cost --------------------------------------

_TWITTER_EPOCH_MS = 1288834974657


def aired(episode_id: str, published_at: str = "") -> datetime | None:
    """When a show went live.

    A broadcast's id is the id of the post that started it, and a post id
    carries the millisecond it was made. A YouTube upload has only its
    date, and the show airs at 1pm Pacific, which is 20:00 UTC.
    """
    match = re.fullmatch(r"x-(\d{15,20})", str(episode_id or ""))
    if match:
        stamp = (int(match.group(1)) >> 22) + _TWITTER_EPOCH_MS
        return datetime.fromtimestamp(stamp / 1000, UTC)
    try:
        day = date.fromisoformat(str(published_at or "")[:10])
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, 20, tzinfo=UTC)


def daily_mark(when: datetime) -> date:
    """The midnight UTC nearest a moment.

    Price history older than three months comes one point a day, at
    00:00 UTC. A show that starts at 20:00 is four hours before the next
    one and twenty after the last, so it is priced at the next. The same
    rule for every row, whichever way it falls.
    """
    when = when.astimezone(UTC)
    floor = datetime(when.year, when.month, when.day, tzinfo=UTC)
    return (floor + timedelta(days=1)).date() if when - floor >= timedelta(hours=12) else floor.date()


def moved(stance: str, then: float | None, now: float | None) -> float | None:
    """Percent the price moved in the direction of the call.

    A short that fell 20% is +20. None when either price is missing or
    nonsense, and the row then shows no number rather than a wrong one.
    """
    if not then or not now or then <= 0 or now <= 0:
        return None
    change = (now - then) / then * 100.0
    return round(-change if stance == "short" else change, 2)


def tally(rows: list[dict]) -> dict:
    """The line above the board: how many, how many up, the middle one.

    Counted over rows that have a number. The median and not the mean: one
    coin that went up twentyfold would otherwise be the whole summary.
    """
    scored = sorted(r["moved_pct"] for r in rows
                    if isinstance(r.get("moved_pct"), (int, float)))
    count = len(scored)
    if not count:
        return {"calls": len(rows), "priced": 0, "up": 0, "down": 0,
                "median_pct": None, "best_pct": None, "worst_pct": None}
    middle = (scored[count // 2] if count % 2
              else (scored[count // 2 - 1] + scored[count // 2]) / 2)
    return {"calls": len(rows), "priced": count,
            "up": sum(1 for x in scored if x > 0),
            "down": sum(1 for x in scored if x < 0),
            "median_pct": round(middle, 2),
            "best_pct": scored[-1], "worst_pct": scored[0]}


# --- the board, as it is served ------------------------------------------------

# What a row tells the page. The listing is how the price was looked up and
# stays on the server.
_SHOWN = ("id", "episode_id", "show", "aired", "date", "speaker", "asset",
          "symbol", "class", "stance", "kind", "target", "quote",
          "start_seconds", "timestamp", "link", "price_then", "then_at")
# A fresh price this far from the one the board was built with is not a
# market move, it is a different asset answering to the same symbol or a
# bad tick. The built price is kept and the row loses nothing.
_SANE = (0.05, 20.0)


def board(shipped: dict, live: dict[str, float] | None = None,
          live_at: str | None = None) -> dict:
    """The file the build wrote, with today's prices laid over it.

    `live` is listing -> price, from whenever the source last answered. It
    may be empty, partial or absent. Every row already has the price it
    was built with, so the board is complete without it and says which
    day its prices are from.
    """
    live = {str(k).upper(): v for k, v in (live or {}).items()}
    rows, fresh_rows = [], 0
    for kept in shipped.get("calls") or []:
        row = {field: kept.get(field) for field in _SHOWN}
        built = kept.get("price_last")
        fresh = live.get(str(kept.get("listing") or "").upper())
        if fresh and built and not _SANE[0] <= fresh / built <= _SANE[1]:
            fresh = None
        row["price_now"] = fresh or built
        row["now_at"] = live_at if fresh else kept.get("last_at")
        # An exit has no direction to score: he is out.
        row["moved_pct"] = (None if kept.get("kind") == "exit" else
                            moved(str(kept.get("stance")), kept.get("price_then"),
                                  row["price_now"]))
        fresh_rows += bool(fresh)
        rows.append(row)
    return {
        "calls": rows,
        "summary": tally(rows),
        "by_speaker": {host: tally([r for r in rows if r["speaker"] == host])
                       for host in HOSTS},
        "shows": len({r["episode_id"] for r in rows}),
        "live": fresh_rows > 0,
        "priced_at": live_at if fresh_rows else shipped.get("priced_at"),
        "built_at": shipped.get("built_at"),
    }

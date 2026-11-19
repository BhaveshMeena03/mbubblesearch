"""Price levels named on air, and when the market got there.

The calls board scores views. This keeps the numbers: "I think I want
to buy Zcash at like 750", "Bitcoin goes to like 65", "the bull is only
cancelled on a close below 58k". A level is something the market can
reach, and the day it does is the day that clip is worth posting.

Same checks as the calls board, and each one can only remove a row:

  the words   the quote is in the transcript word for word, on the line
              the model pointed at (app/calls.py does the finding)
  the voice   the line it starts on is a host's
  the number  the number he said is in the quote: "65" for 65,000 is
              fine, a level the model worked out from context is not
  a person    nothing ships unless data/levels_verdicts.json says keep

Which way a level points is never taken from the model. It comes from
the price when he said it: under that price the level is reached on a
low, over it on a high.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from app import calls
from app.schemas import his_own

# buy     he would buy or add there
# sell    he would sell or take profit there
# target  he expects the price to get there, up or down
# line    a level he says decides something: hold it or lose it
KINDS = ("buy", "sell", "target", "line")

# How close counts as near, in percent of today's price.
NEAR_PCT = 5.0
# A level further than this from the price when he said it is a misread
# ("30" heard as a price when he meant 30 percent), not a level. The top
# is wide on purpose: "Solana from $66 to $600 this cycle" is 9x and real.
SANE = (0.2, 15.0)

_NUMBER = re.compile(
    r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*"
    r"(k|m|b|thousand|grand|million|billion)?(?![a-z])", re.I)
_SCALE = {"k": 1e3, "thousand": 1e3, "grand": 1e3,
          "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
_MARKET_CAP = re.compile(r"\b(?:market ?cap|mark cap|mcap|fdv|valuation)\b", re.I)


def numbers_in(text: str) -> list[float]:
    """Every number in a line, with "k", "million" and the like applied."""
    found = []
    for match in _NUMBER.finditer(text or ""):
        whole = match.group(1).replace(",", "")
        value = float(f"{whole}.{match.group(2)}" if match.group(2) else whole)
        found.append(value * _SCALE.get((match.group(3) or "").lower(), 1.0))
    return found


def said_in(level: float, quote: str) -> bool:
    """True when the level is a number in the quote.

    People drop the thousands: "Bitcoin goes to like 65" is 65,000. So a
    bare number may stand for itself times a thousand or a million, and
    nothing else. Within half a percent, for "1.8" meaning 1,800.
    """
    if level <= 0:
        return False
    for number in numbers_in(quote):
        for scale in (1.0, 1e3, 1e6, 1e9):
            if abs(number * scale - level) <= level * 0.005:
                return True
    return False


@dataclass(frozen=True)
class Level:
    """One host, one asset, one number, one moment."""

    id: str               # "<episode>:<line>:<symbol>:<level>"
    episode_id: str
    line: int
    last_line: int
    start_seconds: float
    speaker: str
    asset: str
    symbol: str
    asset_class: str
    kind: str
    level: float
    quote: str            # cut from the transcript

    def as_row(self) -> dict:
        return asdict(self)


def own_video(episode: dict) -> bool:
    """A video Ansem posted himself ("Ansem on X"): nobody else talks in
    it, so it carries no voice labels, and it was heard when he posted
    it rather than at a time into a live show."""
    return his_own(episode.get("show"))


def voices(episode: dict, labels: dict[str, dict]) -> dict[str, str]:
    """Whose voice each line is in: the labels on file, or, for one of
    his own videos, his on every line."""
    found = labels.get(str(episode.get("episode_id")), {})
    if not found and own_video(episode):
        return {str(i): "Ansem" for i in range(len(episode.get("segments") or []))}
    return found


def level_id(episode_id: str, line: int, symbol: str, level: float) -> str:
    return f"{episode_id}:{line}:{symbol}:{level:g}"


def accept(raw: dict, episode: dict, labels: dict[str, str],
           hosts: tuple[str, ...] = calls.HOSTS) -> tuple[Level | None, str]:
    """A model's claim, held against the transcript.

    Returns the level, or None and the reason it was refused.
    """
    segments = episode.get("segments") or []
    try:
        near = int(raw.get("line"))
    except (TypeError, ValueError):
        return None, "no line"
    kind = str(raw.get("kind") or "").lower()
    if kind not in KINDS:
        return None, "no kind"
    try:
        level = float(raw.get("level"))
    except (TypeError, ValueError):
        return None, "no level"
    if not level > 0:
        return None, "no level"
    symbol = calls._symbol(raw.get("symbol"))
    if not symbol:
        return None, "no symbol"
    span = calls.locate(str(raw.get("quote") or ""), segments, near)
    if span is None:
        return None, "quote is not in the transcript"
    quote = calls.cut(segments, span)
    if _MARKET_CAP.search(quote):
        return None, "a market cap is not a price"
    if not said_in(level, quote):
        return None, "the number is not in the quote"
    if calls.tactical(quote):
        return None, calls.tactical(quote)
    who = calls.voice(labels, span.first, span.last, hosts)
    if who is None:
        return None, "voice not known"
    claimed = str(raw.get("speaker") or "")
    if claimed and claimed != who:
        return None, "voice is not who the model named"
    return Level(
        id=level_id(str(episode["episode_id"]), span.first, symbol, level),
        episode_id=str(episode["episode_id"]),
        line=span.first, last_line=span.last,
        start_seconds=float(segments[span.first].get("t", 0.0)),
        speaker=who, asset=" ".join(str(raw.get("asset") or "").split()),
        symbol=symbol, asset_class=str(raw.get("asset_class") or "crypto").lower(),
        kind=kind, level=level, quote=quote,
    ), ""


def one_per_show(found: list[Level]) -> list[Level]:
    """The same number for the same coin in one show is one level: the
    first time it was said."""
    kept: dict[tuple, Level] = {}
    for item in sorted(found, key=lambda x: (x.episode_id, x.line)):
        key = (item.episode_id, item.speaker, item.symbol, item.kind, round(item.level, 6))
        kept.setdefault(key, item)
    return sorted(kept.values(), key=lambda x: (x.episode_id, x.line))


# --- what the market did ---------------------------------------------------------

Bar = tuple[datetime, float | None, float | None, float | None]   # start, high, low, close


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and value > 0 else None


def read_bars(payload: object) -> list[Bar]:
    """(start, high, low, close) out of a chart reply, oldest first.

    [] for anything that is not a chart: an error body, a symbol that
    does not exist, a reply cut short.
    """
    try:
        result = payload["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return []
    highs, lows, closes = (quote.get(k) or [] for k in ("high", "low", "close"))
    bars = []
    for index, stamp in enumerate(stamps):
        try:
            start = datetime.fromtimestamp(int(stamp), UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        bars.append((start, _nth(highs, index), _nth(lows, index), _nth(closes, index)))
    return sorted(bars)


def _nth(values: list, index: int) -> float | None:
    return _number(values[index]) if index < len(values) else None


def price_at(bars: list[Bar], when: datetime, width: timedelta = timedelta(hours=1)) -> float | None:
    """The close of the last bar that had finished when he spoke.

    Not older than six hours: a gap that long means the source has no
    price for that evening, and a guess is not printed.
    """
    done = [(start, close) for start, _, _, close in bars
            if start + width <= when and close is not None]
    if not done:
        return None
    start, close = max(done)
    return close if when - (start + width) <= timedelta(hours=6) else None


def side(level: float, then: float) -> str:
    """Under the price when it was said, a level is reached on the way
    down; over it, on the way up."""
    return "below" if level < then else "above"


def sane(level: float, then: float | None) -> bool:
    return bool(then) and SANE[0] <= level / then <= SANE[1]


def status(level: float, then: float, said_at: datetime, bars: list[Bar],
           now: float | None = None, now_at: datetime | None = None) -> dict:
    """Where a level stands: reached, and when, or how far is left.

    Only bars that started after he spoke count. The bar he spoke in
    holds prices from before he said anything.
    """
    way = side(level, then)
    after = [b for b in bars if b[0] >= said_at]
    hit_at, best = None, None
    for start, high, low, _ in after:
        mark = low if way == "below" else high
        if mark is None:
            continue
        best = mark if best is None else (min(best, mark) if way == "below" else max(best, mark))
        if hit_at is None and (mark <= level if way == "below" else mark >= level):
            hit_at = start
    closes = [(b[0], b[3]) for b in after if b[3] is not None]
    if now is None and closes:
        now_at, now = closes[-1]
    if hit_at is None and now is not None and (now <= level if way == "below" else now >= level):
        hit_at = now_at
    if now is not None:
        best = now if best is None else (min(best, now) if way == "below" else max(best, now))
    away = None
    if now and hit_at is None:
        away = round((now - level) / now * 100 if way == "below" else (level - now) / now * 100, 2)
    return {
        "state": "hit" if hit_at else "open", "side": way,
        "hit_at": hit_at.isoformat(timespec="minutes") if hit_at else None,
        "best": best, "price_now": now,
        "now_at": now_at.isoformat(timespec="minutes") if now_at else None,
        "away_pct": away,
    }


def is_near(check: dict, pct: float = NEAR_PCT) -> bool:
    return (check.get("state") == "open" and isinstance(check.get("away_pct"), (int, float))
            and 0 <= check["away_pct"] <= pct)


def _moment(stamp: str) -> datetime:
    when = datetime.fromisoformat(stamp)
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def in_time(row: dict, when: datetime) -> bool:
    """A target he gave a date ("by end of the year") counts only up to
    that date. After it, reaching the number says nothing he said."""
    by = row.get("by")
    return not by or when.date() <= datetime.fromisoformat(str(by)[:10]).date()


def alerts(rows: list[dict], seen: dict, since: datetime,
           now: datetime | None = None) -> list[dict]:
    """What is new since the last look: a level reached, or a level now
    within NEAR_PCT. Each one once.

    `seen` is {"hit": [ids], "near": [ids]}. A level reached before
    `since` was reached while nobody was looking for it, and is recorded
    without an alert: the first run would otherwise post six months of
    history in one morning.

    Left out: a quiet level (a support he expected to hold, where the
    only news is that he was wrong), a dated target past its date, and
    the same number from the same host said again in a later show: he
    named Solana 600 on three shows, and that is one post, from the
    first time.
    """
    now = now or datetime.now(UTC)
    found: dict[tuple, dict] = {}
    hit, near = set(seen.get("hit") or []), set(seen.get("near") or [])
    for row in rows:
        if row.get("quiet"):
            continue
        check = row.get("check") or {}
        ident = row["id"]
        alert = None
        if check.get("state") == "hit" and ident not in hit:
            when = _moment(check["hit_at"])
            if when >= since and in_time(row, when):
                alert = "hit"
        elif is_near(check) and ident not in near and ident not in hit and in_time(row, now):
            alert = "near"
        if alert is None:
            continue
        key = (alert, row.get("speaker"), row.get("symbol"), float(row.get("level") or 0))
        held = found.get(key)
        if held is None or str(row.get("said_at", "")) < str(held.get("said_at", "")):
            found[key] = {"alert": alert, **row}
    return sorted(found.values(), key=lambda a: (a["alert"] != "hit", str(a.get("said_at", ""))))


def remember(rows: list[dict], seen: dict) -> dict:
    """`seen` with everything reached or near today written in."""
    hit = set(seen.get("hit") or [])
    near = set(seen.get("near") or [])
    for row in rows:
        check = row.get("check") or {}
        if check.get("state") == "hit":
            hit.add(row["id"])
        elif is_near(check):
            near.add(row["id"])
    return {"hit": sorted(hit), "near": sorted(near)}


# --- the post ----------------------------------------------------------------------

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")


def money(value: float | None) -> str:
    if value is None:
        return "?"
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4g}"


def spoken_date(day: str) -> str:
    """"2026-10-29" as "october 29, 2026"."""
    year, month, date_ = (int(x) for x in day[:10].split("-"))
    return f"{_MONTHS[month - 1]} {date_}, {year}"


def plain(text: str) -> str:
    """Lower case, straight quotes, no dashes: how the account writes."""
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"\s*[–—]\s*", ", ", text)
    return " ".join(text.lower().split())


def _who(speaker: str) -> str:
    return "banks" if "banks" in speaker.lower() else speaker.split()[0].lower()


def headline(row: dict, alert: str) -> str:
    """The first line of the post: what just happened, in one breath."""
    sym, who, at = row["symbol"].lower(), _who(row["speaker"]), money(row["level"])
    check = row.get("check") or {}
    way = check.get("side")
    if alert == "near":
        noun = {"buy": "buy", "sell": "sell", "target": "target", "line": "line"}[row["kind"]]
        return f"{sym} is {check.get('away_pct'):g}% from {who}'s {at} {noun}."
    if row["kind"] == "buy":
        return (f"{sym} just hit {who}'s buy at {at}." if way == "below"
                else f"{sym} just broke {at}, where {who} said he'd buy.")
    if row["kind"] == "sell":
        return (f"{sym} just hit {who}'s sell at {at}." if way == "above"
                else f"{sym} just fell to {at}, where {who} said he'd sell.")
    if row["kind"] == "target":
        return (f"{sym} just hit {who}'s {at} target." if way == "above"
                else f"{sym} just fell to {at}, where {who} said it was going.")
    return (f"{sym} just lost {at}, the line {who} drew." if way == "below"
            else f"{sym} just took back {at}, the line {who} drew.")


def draft(row: dict, alert: str) -> str:
    """A post for @mbubbleSearch, one paragraph per line, ready to check.

    The quote and every number come from the row. Whoever posts it still
    reads it: a level can be right and the moment still not worth a post.
    """
    check = row.get("check") or {}
    sym = row["symbol"].lower()
    return "\n\n".join([
        headline(row, alert),
        f"{'his video on x' if row.get('own') else 'market bubble'}, "
        f"{spoken_date(row['date'])}, with {sym} at {money(row['price_then'])}:",
        f'"{plain(row["quote"])}"',
        f"{sym} is {money(check.get('price_now'))} now.",
        row["link"],
    ])

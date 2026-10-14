"""What an asset cost when a host named it, and what it costs now.

One source for both, and for stocks as well as tokens: Yahoo's chart data,
which gives a bar a day going back years and names the exact listing in
its reply. A token is asked for by Yahoo's own symbol, which carries the
listing's number ("STRK22691-USD"), so two coins that share a ticker
cannot be confused. data/call_assets.json holds that symbol for every
asset on the board, and it is filled in by hand.

The parsing is separate from the fetching. Everything that decides a
number is a pure function over a payload, so a test can hold it against a
reply saved from the real thing.

Nothing here is on the path of a search. The board is built ahead of time
with prices in it, and the page works from that file. Fresh prices are an
overlay, and when the source is down the page shows the ones it was built
with and says when they are from.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SPARK = "https://query1.finance.yahoo.com/v8/finance/spark"
# Without a browser's user agent the endpoint answers 429 to everybody.
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
# Symbols per request to the batch endpoint.
BATCH = 20

Bar = tuple[datetime, float | None, float | None]      # start, open, close


def _number(value: object) -> float | None:
    """A price, or None. A bar with no trades comes back as null, and a
    zero or a negative is not a price."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def read_chart(payload: object) -> tuple[dict, list[Bar]]:
    """The listing and its daily bars, out of a chart reply.

    ({}, []) for anything that is not one: an error body, a symbol that
    does not exist, a reply cut short.
    """
    try:
        result = payload["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        opens, closes = quote.get("open") or [], quote.get("close") or []
    except (KeyError, IndexError, TypeError):
        return {}, []
    bars: list[Bar] = []
    for index, stamp in enumerate(stamps):
        try:
            start = datetime.fromtimestamp(int(stamp), UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        bars.append((start,
                     _number(opens[index] if index < len(opens) else None),
                     _number(closes[index] if index < len(closes) else None)))
    return dict(result.get("meta") or {}), bars


def read_highs(payload: object) -> list[tuple[date, float]]:
    """(day, highest price that day) out of a chart reply, oldest first.

    For asking whether a target was ever touched: a close can sit under a
    level the price reached at noon. A day with no high is left out.
    """
    try:
        result = payload["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        highs = result["indicators"]["quote"][0].get("high") or []
    except (KeyError, IndexError, TypeError):
        return []
    found = []
    for index, stamp in enumerate(stamps):
        high = _number(highs[index] if index < len(highs) else None)
        try:
            day = datetime.fromtimestamp(int(stamp), UTC).date()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if high is not None:
            found.append((day, high))
    return sorted(found)


def is_listing(meta: dict, symbol: str, name: str) -> bool:
    """True when a reply is for the asset that was asked for.

    The symbol has to come back as it went out, and the listing's name has
    to contain the name on file. Asked for a symbol that has been reused
    or withdrawn, the source can answer with something else or nothing,
    and either way the number is not ours to print.
    """
    if str(meta.get("symbol") or "").upper() != symbol.upper():
        return False
    told = f"{meta.get('longName') or ''} {meta.get('shortName') or ''}".lower()
    return bool(name) and name.lower() in told


def at_mark(bars: list[Bar], mark: date) -> float | None:
    """A token's price at 00:00 UTC on `mark`.

    Its bars are UTC days, so that is the open of the bar that starts
    then, or the close of the day before, which is the same moment. A day
    the source has no bar for is given up on rather than borrowed from a
    neighbour: two days is a long time in a small coin.
    """
    for start, opened, _ in bars:
        if start.date() == mark and start.hour == 0 and opened is not None:
            return opened
    for start, _, closed in bars:
        if start.date() == mark - timedelta(days=1) and closed is not None:
            return closed
    return None


def close_by(bars: list[Bar], when: datetime) -> float | None:
    """A stock's last close as a show went live.

    The show airs at 1pm Pacific, which is the closing bell in New York,
    so the session that opened before it started is the price anybody
    listening could have paid next. Not older than four days: across a
    long weekend that is still the last close, and beyond it the listing
    was not trading.
    """
    before = [(start, closed) for start, _, closed in bars
              if start <= when and closed is not None]
    if not before:
        return None
    start, closed = max(before)
    return closed if when - start <= timedelta(days=4) else None


def latest(bars: list[Bar]) -> tuple[float, datetime] | None:
    """The most recent price in a run of bars, and the day it is from."""
    for start, _, closed in reversed(bars):
        if closed is not None:
            return closed, start
    return None


def read_spark(payload: object) -> dict[str, float]:
    """symbol -> latest price, out of a batch reply. Whatever is missing
    or malformed is left out, and its row keeps the price it was built
    with."""
    found: dict[str, float] = {}
    if not isinstance(payload, dict):
        return found
    for symbol, row in payload.items():
        if not isinstance(row, dict):
            continue
        closes = row.get("close") or []
        price = next((p for p in (_number(c) for c in reversed(closes)) if p), None)
        if price is not None:
            found[str(symbol).upper()] = price
    return found


async def chart_reply(symbol: str, start: date, end: date, *,
                      client: httpx.AsyncClient, interval: str = "1d") -> object:
    """The source's reply for one listing between two days, inclusive."""
    first = datetime(start.year, start.month, start.day, tzinfo=UTC)
    last = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
    reply = await client.get(
        CHART.format(symbol=symbol), headers=HEADERS,
        params={"period1": int(first.timestamp()),
                "period2": int(last.timestamp()), "interval": interval})
    reply.raise_for_status()
    return reply.json()


async def chart(symbol: str, start: date, end: date, *,
                client: httpx.AsyncClient, interval: str = "1d") -> tuple[dict, list[Bar]]:
    """Bars for one listing between two days, inclusive. A bar a day
    unless an hourly interval is asked for."""
    return read_chart(await chart_reply(symbol, start, end, client=client,
                                        interval=interval))


async def now(symbols: list[str], *, client: httpx.AsyncClient) -> dict[str, float]:
    """Latest prices for many listings. Partial is fine, empty is fine:
    whoever asks already has a price for every row."""
    found: dict[str, float] = {}
    wanted = sorted({s.upper() for s in symbols if s})
    for at in range(0, len(wanted), BATCH):
        try:
            reply = await client.get(
                SPARK, headers=HEADERS,
                params={"symbols": ",".join(wanted[at:at + BATCH]),
                        "range": "1d", "interval": "1d"})
            reply.raise_for_status()
            found.update(read_spark(reply.json()))
        except Exception as exc:                                # noqa: BLE001
            logger.warning("live prices unavailable for a batch (%s)",
                           type(exc).__name__)
    return found

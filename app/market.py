"""Live market data for assets the hosts discussed.

The dashboard says what was said about a token. This adds what the market
is doing with it right now, and — separately — a route to trade it.

Those are two different questions and are answered independently:

  price   comes from Jupiter when the asset lives on Solana, otherwise from
          CoinGecko. Most discussed assets can be priced.
  trade   is offered ONLY when the ticker resolves to a Jupiter-verified
          Solana mint with real liquidity. A price is information; a trade
          link is a loaded gun, and it is held to a far higher bar.

An asset can therefore be priced but not tradeable (BTC, XRP, NVDA). It can
never be tradeable without also being priced.

Safety note, and it is the whole reason `pick_token` is a separate pure
function: a ticker in an auto-generated caption is not an identity. Several
tokens share a symbol, and the scam ones are deliberately named to collide.
Resolving "$PENGU" to the wrong mint would put a swap link to a honeypot on
a public page. So resolution refuses to guess — unverified, symbol
mismatched or thin liquidity all return None, and the UI simply shows no
market data rather than a wrong one.

Nothing here places an order. It resolves a mint and builds a link the user
must click and confirm in their own wallet.
"""
from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

JUPITER_SEARCH = "https://lite-api.jup.ag/tokens/v2/search"
JUPITER_SWAP = "https://jup.ag/swap/USDC-{mint}"
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"

# How deep into the market-cap ranking to build the symbol table. Deeper
# covers more of the long tail; each page is one upstream call against a free
# tier, so this trades coverage for politeness. 4 pages ~= the top 1000.
COINGECKO_PAGES = 4

# Below this much USD liquidity a swap link is more of a hazard than a
# feature: the price shown would not survive the trade it invites.
MIN_LIQUIDITY_USD = 50_000

# Native SOL is not a searchable SPL token in the usual way; it is the one
# identity worth hard-coding, and it is the canonical wrapped-SOL mint.
WSOL_MINT = "So11111111111111111111111111111111111111112"

# A mint is interpolated into a URL we publish, so it is validated as strict
# base58 (no 0, O, I or l) at Solana address length before it is trusted that
# far. Anything else from upstream is treated as absent, not as a string.
_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# What we accept as a ticker from a URL path. Deliberately narrow: this is
# forwarded to an upstream API, and everything outside this set is either a
# typo or an attempt at something.
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$")


# --------------------------------------------------------------------------
# Tokenized equities
# --------------------------------------------------------------------------
#
# The hosts talk about NVDA and TSLA as much as they talk about SOL, and a
# stock could only ever be a dead row here: not a Solana mint, so no price
# and no route, which is why "stock" sat in the unpriceable set. Backed's
# xStocks put the same equities on Solana as verified SPL tokens -- NVDA
# trades as NVDAx -- so the question stops being "is this a stock" and
# becomes the one this file already answers: does the ticker resolve to a
# verified mint with real liquidity.
#
# It is also where guessing would do the most damage. Searching Jupiter for
# NVDAx on 2026-09-24 returned the real token, verified with $2.99M of
# liquidity, followed by three impostors at about $6.4K -- one copying the
# symbol exactly, another naming itself "Nvidia Stonk". TSLAx returned an
# impostor that copies the real name, "Tesla xStock", character for
# character. Every fake was unverified, which is the gate that matters.
STOCK_SUFFIX = "x"

# Higher than the crypto floor, on purpose. The real xStocks sampled sat
# between $0.67M and $6.1M; every impostor sat between $3K and $23K. 250K is
# comfortably between the two and still admits a thinly traded real listing.
STOCK_MIN_LIQUIDITY_USD = 250_000

# Backed names each one "<Company> xStock". Not sufficient alone -- an
# impostor copies it -- but it is one more thing a fake has to clear, and it
# costs a genuine listing nothing.
_XSTOCK_NAME_RE = re.compile(r"xstock", re.I)

# Asset classes that can only ever be reached through a tokenized share.
# Deliberately NOT routed to CoinGecko afterwards: CoinGecko prices crypto,
# so "AAPL" there is whatever memecoin claimed the ticker, and a stock row
# showing a memecoin's price is worse than a stock row showing nothing.
TOKENIZED_CLASSES = frozenset({"stock", "index"})


def valid_mint(mint: object) -> bool:
    """True only for a well-formed Solana mint address."""
    return isinstance(mint, str) and bool(_MINT_RE.match(mint))


def clean_symbol(symbol: object) -> str | None:
    """Normalised ticker, or None if it is not one."""
    if not isinstance(symbol, str):
        return None
    s = symbol.strip()
    return s.upper() if _SYMBOL_RE.match(s) else None


def _ticker(value: object) -> str:
    """Normalise a ticker for comparison.

    A leading '$' is decoration, not identity. Jupiter lists dogwifhat with
    the symbol "$WIF" while the extractor stores "WIF", so an exact compare
    rejected a verified token with $5.6M of liquidity and the dashboard
    showed no market data for one of the best known assets on Solana.

    This is not a loosening of the rule. assets.canonical() already strips
    the same character on the way in, so the two sides were normalising
    differently; this makes them agree. Two genuinely different tickers
    still cannot match.
    """
    return str(value or "").strip().lstrip("$").lower()


def pick_token(candidates: list[dict], symbol: str, *,
               min_liquidity: float = MIN_LIQUIDITY_USD) -> dict | None:
    """The verified Solana token for `symbol`, or None if not confident.

    Rules, in order — every one of them can only ever reject:
      * symbol must match exactly once normalised; no fuzzy matching
      * the token must be Jupiter-verified
      * liquidity must clear `min_liquidity`
    Among survivors the deepest liquidity wins, which is deterministic and
    is also the one a swap would actually route through.
    """
    want = _ticker(symbol)
    if not want:
        return None

    viable = [
        c for c in candidates
        if _ticker(c.get("symbol")) == want
        and c.get("isVerified") is True
        and valid_mint(c.get("id"))
        and _as_float(c.get("liquidity")) >= min_liquidity
    ]
    if not viable:
        return None
    return max(viable, key=lambda c: _as_float(c.get("liquidity")))


def pick_tokenized_stock(candidates: list[dict], ticker: str, *,
                         min_liquidity: float = STOCK_MIN_LIQUIDITY_USD,
                         ) -> dict | None:
    """The verified tokenized share for a stock ticker, or None.

    Same shape as pick_token and the same contract -- every rule can only
    reject -- with two additions, because the impostors here are better at
    impersonation than a memecoin squatting on a ticker:

      * the symbol must be the ticker plus the suffix, so NVDA only ever
        matches NVDAx and never the "NVDA" memecoin that shares the name
      * the name must say xStock, which is the issuer's own convention

    Verification is still what does the real work. Among the eight tokens
    Jupiter returned for NVDAx and TSLAx, exactly one of each was verified,
    and it was the right one both times.
    """
    want = _ticker(ticker)
    if not want:
        return None
    want += STOCK_SUFFIX

    viable = [
        c for c in candidates
        if _ticker(c.get("symbol")) == want
        and c.get("isVerified") is True
        and valid_mint(c.get("id"))
        and _XSTOCK_NAME_RE.search(str(c.get("name") or ""))
        and _as_float(c.get("liquidity")) >= min_liquidity
    ]
    if not viable:
        return None
    return max(viable, key=lambda c: _as_float(c.get("liquidity")))


def _as_float(v: object) -> float:
    """Upstream numerics are not guaranteed; a bad one must not raise."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and f not in (float("inf"), float("-inf")) else 0.0


def summarise(token: dict) -> dict:
    """A Jupiter hit as a quote: priced, and tradeable because it resolved."""
    stats = token.get("stats24h") or {}
    return {
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "price_usd": token.get("usdPrice"),
        "change_24h_pct": stats.get("priceChange"),
        "source": "jupiter",
        # The on-chain ticker when the thing being priced is a share rather
        # than the asset itself. Null for ordinary crypto, and present in
        # both cases for the same reason `trade` is: a key that is sometimes
        # absent gets skipped, a key that is null gets handled.
        "tokenized_as": None,
        "trade": {
            "venue": "Jupiter",
            "chain": "solana",
            "mint": token["id"],
            "liquidity_usd": token.get("liquidity"),
            "swap_url": JUPITER_SWAP.format(mint=token["id"]),
        },
    }


def from_coingecko(row: dict) -> dict:
    """A CoinGecko row as a quote: priced, explicitly NOT tradeable here.

    `trade` is None rather than absent so that every consumer has to look at
    it. An omitted key gets skipped; a null gets handled.
    """
    return {
        "symbol": str(row.get("symbol", "")).upper(),
        "name": row.get("name"),
        "price_usd": row.get("current_price"),
        "change_24h_pct": row.get("price_change_percentage_24h"),
        "source": "coingecko",
        "tokenized_as": None,
        "trade": None,
    }


async def lookup(symbol: str, *, client: httpx.AsyncClient | None = None,
                 timeout: float = 8.0) -> dict | None:
    """Resolve one symbol to live Solana market data, or None."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    symbol = clean_symbol(symbol)
    if symbol is None:
        return None
    try:
        # Fixed URL, ticker passed as an encoded parameter — never interpolated.
        r = await client.get(JUPITER_SEARCH, params={"query": symbol})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 — market data must never 500 a page
        logger.warning("jupiter lookup failed for %s: %s", symbol, exc)
        return None
    finally:
        if owns:
            await client.aclose()

    if not isinstance(data, list):
        return None
    hit = pick_token(data, symbol)
    return summarise(hit) if hit else None


def summarise_stock(token: dict, ticker: str) -> dict:
    """A tokenized share as a quote, reported under the ticker discussed.

    The show says NVDA, so the row says NVDA. The share it actually routes
    through is NVDAx and that is stated rather than hidden: somebody
    clicking through is buying a Backed-issued token on Solana, not stock
    in a brokerage account, and the page has to be able to say so.
    """
    quote = summarise(token)
    quote["symbol"] = ticker
    quote["tokenized_as"] = token.get("symbol")
    return quote


async def lookup_stock(ticker: str, *, client: httpx.AsyncClient | None = None,
                       timeout: float = 8.0) -> dict | None:
    """Resolve a stock ticker to its tokenized share on Solana, or None."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    ticker = clean_symbol(ticker)
    if ticker is None:
        return None
    try:
        # Searched by the ON-CHAIN symbol, because that is what Jupiter
        # indexes: a query for "NVDA" returns the memecoin of that name and
        # not the share.
        r = await client.get(JUPITER_SEARCH,
                             params={"query": ticker + STOCK_SUFFIX})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 — market data must never 500 a page
        logger.warning("jupiter stock lookup failed for %s: %s", ticker, exc)
        return None
    finally:
        if owns:
            await client.aclose()

    if not isinstance(data, list):
        return None
    hit = pick_tokenized_stock(data, ticker)
    return summarise_stock(hit, ticker) if hit else None


# --------------------------------------------------------------------------
# CoinGecko — prices for crypto that does not live on Solana
# --------------------------------------------------------------------------

def build_symbol_table(rows: list[dict]) -> dict[str, dict]:
    """symbol -> row, keeping the highest market cap for each symbol.

    Symbol collisions are the norm on CoinGecko: dozens of tokens call
    themselves BTC. The listing is requested market-cap descending, so the
    first sighting of a symbol is the largest and every later one is
    discarded. That is a heuristic, not proof of identity — which is exactly
    why this path never grants a trade link.
    """
    table: dict[str, dict] = {}
    for row in rows:
        sym = row.get("symbol")
        if not isinstance(sym, str) or not sym.strip():
            continue
        if row.get("current_price") is None:
            continue                      # a row with no price prices nothing
        table.setdefault(sym.strip().upper(), row)
    return table


async def fetch_coingecko_table(*, client: httpx.AsyncClient | None = None,
                                pages: int = COINGECKO_PAGES,
                                timeout: float = 12.0) -> dict[str, dict]:
    """Top-ranked coins as a symbol table. Partial results beat none."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    rows: list[dict] = []
    try:
        for page in range(1, pages + 1):
            try:
                r = await client.get(COINGECKO_MARKETS, params={
                    "vs_currency": "usd", "order": "market_cap_desc",
                    "per_page": 250, "page": page,
                })
                r.raise_for_status()
                batch = r.json()
            except Exception as exc:  # noqa: BLE001
                # Rate limited or down. Keep whatever earlier pages returned:
                # a table covering the top 500 is far better than an empty one.
                logger.warning("coingecko page %d failed: %s", page, exc)
                break
            if not isinstance(batch, list) or not batch:
                break
            rows.extend(batch)
    finally:
        if owns:
            await client.aclose()
    return build_symbol_table(rows)


# --------------------------------------------------------------------------
# The combined answer
# --------------------------------------------------------------------------

async def quote(symbol: str, *, asset_class: str | None = None,
                coingecko_table: dict[str, dict] | None = None,
                client: httpx.AsyncClient | None = None) -> dict | None:
    """Price plus, only where it is earned, a trade route.

    Jupiter is tried first because it answers both questions at once. Falling
    back to CoinGecko yields a price with `trade: None` — priced, not
    tradeable, which is the correct outcome for BTC, XRP and the rest.

    A stock takes a different road entirely and never reaches CoinGecko:
    see TOKENIZED_CLASSES for why a ticker like AAPL must not be looked up
    in a crypto price table.
    """
    ticker = clean_symbol(symbol)
    if ticker is None:
        return None

    if asset_class in TOKENIZED_CLASSES:
        return await lookup_stock(ticker, client=client)

    on_solana = await lookup(ticker, client=client)
    if on_solana is not None:
        return on_solana

    if coingecko_table:
        row = coingecko_table.get(ticker)
        if row is not None:
            return from_coingecko(row)
    return None

"""Build the board of calls from the ones a person kept.

    .venv/bin/python scripts/build_calls.py            # price what is new, refresh latest prices
    .venv/bin/python scripts/build_calls.py --offline  # no network: rebuild rows, keep prices on file

Three files go in, all of them read by a person:

    data/calls_candidates.json   what scripts/extract_calls.py proposed and
                                 app/calls.py did not refuse
    data/calls_verdicts.json     id -> {"keep": true|false, "why": "..."}
                                 plus, on a kept row, corrections to
                                 "symbol", "stance", "kind" or "target",
                                 and "quote" or "line" for a better cut
                                 of the same moment. A call the model
                                 missed can be added with "by_hand".
                                 Either way it is checked like any other.
    data/call_assets.json        ticker -> "yahoo", the exact listing it is
                                 priced from; "answers_to", the name that
                                 listing must give back; "name" and
                                 "ticker" as the page shows them

One comes out, data/calls.json, which is what the site serves.

A candidate with no verdict does not ship. A kept call whose ticker has no
listing on file stops the build, and so does one whose quote is no longer
on the line it was found on, which is what a re-transcribed show looks
like. A listing that answers to a different name stops it too. Every one
of those is a wrong row caught before anybody saw it.

A price already on file for the same call and listing is not fetched
again: what a coin cost on a night in June does not change.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import call_prices, calls  # noqa: E402

DATA = ROOT / "data"
CANDIDATES = DATA / "calls_candidates.json"
VERDICTS = DATA / "calls_verdicts.json"
ASSETS = DATA / "call_assets.json"
BOARD = DATA / "calls.json"
LABELS = DATA / "speaker_map.json"
PLAYERS = DATA / "broadcast_links.json"

# The show is made in Los Angeles, and "the 8 October show" is the day it
# was there. In UTC it had already become the 9th.
HOME = ZoneInfo("America/Los_Angeles")
CORRECTABLE = ("symbol", "stance", "kind", "target")
# What a call added by hand has to say for itself. It is checked against
# the transcript and the voice labels exactly as a proposed one is.
BY_HAND = ("episode_id", "line", "speaker", "symbol", "stance", "kind", "quote")
PAUSE = 0.5         # seconds between requests to the price source


def episodes() -> dict[str, dict]:
    plain = DATA / "episodes.json"
    rows = (json.loads(plain.read_text()) if plain.exists()
            else json.loads(gzip.decompress((DATA / "episodes.json.gz").read_bytes())))
    found: dict[str, dict] = {}
    for row in rows:
        found.setdefault(str(row.get("episode_id")), row)
    return found


def stamp(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def link(episode: dict, seconds: float, players: dict[str, str]) -> str:
    """Where the call plays. The broadcast's own player opens at the
    second; a show with no player on file gets the post it aired in."""
    base = players.get(str(episode["episode_id"])) or str(episode.get("url") or "")
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}t={int(seconds)}"


def kept_calls(candidates: dict, verdicts: dict) -> list[dict]:
    """The candidates a person said keep, with that person's corrections,
    and the calls that person added.

    Raises on a verdict that points at nothing: the candidate file was
    rebuilt and the ids moved, and a board built past that would silently
    lose rows.
    """
    by_id = {c["id"]: c for show in candidates.get("shows", {}).values()
             for c in show.get("candidates", [])}
    stale = sorted(i for i, v in verdicts.items()
                   if v.get("keep") and not v.get("by_hand") and i not in by_id)
    if stale:
        raise SystemExit(f"{len(stale)} kept verdict(s) match no candidate, e.g. {stale[:3]}. "
                         "Re-run scripts/extract_calls.py or fix data/calls_verdicts.json.")
    rows = []
    for ident, verdict in verdicts.items():
        if not verdict.get("keep"):
            continue
        if verdict.get("by_hand"):
            missing = [f for f in BY_HAND if verdict.get(f) in (None, "")]
            if missing:
                raise SystemExit(f"{ident}: added by hand without {missing}")
            row = {"id": ident, "asset": "", "target": "", "recut": True,
                   **{f: verdict[f] for f in BY_HAND}}
        else:
            row = dict(by_id[ident])
        for field in CORRECTABLE:
            if field in verdict:
                row[field] = verdict[field]
        if verdict.get("quote"):
            row["quote"], row["recut"] = verdict["quote"], True
        if "line" in verdict:
            # The same call, said better a minute later.
            row["line"], row["recut"] = int(verdict["line"]), True
        if row["stance"] not in calls.STANCES or row["kind"] not in calls.KINDS:
            raise SystemExit(f"{ident}: stance/kind is not one the board knows")
        if row["speaker"] not in calls.HOSTS:
            raise SystemExit(f"{ident}: {row['speaker']!r} is not a host")
        row["symbol"] = str(row.get("symbol") or "").upper()
        rows.append(row)
    return rows


def placed(row: dict, episode: dict, labels: dict[str, str]) -> dict | None:
    """The row as it stands in today's transcript, or None.

    The quote has to be there word for word, starting on the line on
    file, in that host's voice. A verdict can swap the model's choice of
    words for a better cut of the same moment; that cut goes through the
    same check and may start a line or two away.
    """
    segments = episode.get("segments") or []
    span = calls.locate(row["quote"], segments, row["line"])
    if span is None:
        return None
    if not row.get("recut") and span.first != row["line"]:
        return None
    if calls.voice(labels, span.first, span.last) != row["speaker"]:
        return None
    return dict(row, line=span.first, last_line=span.last,
                quote=calls.cut(segments, span),
                start_seconds=float(segments[span.first].get("t", 0.0)))


async def fetch(symbol: str, start, end, client) -> tuple[dict, list]:
    for attempt in range(4):
        try:
            got = await call_prices.chart(symbol, start, end, client=client)
            await asyncio.sleep(PAUSE)
            return got
        except Exception as exc:                                # noqa: BLE001
            if attempt == 3:
                raise SystemExit(f"could not price {symbol}: {type(exc).__name__}") from exc
            await asyncio.sleep(4 * (attempt + 1))
    return {}, []


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="no network: every price has to be on file already")
    args = ap.parse_args()

    shows = episodes()
    labels = json.loads(LABELS.read_text())
    players = json.loads(PLAYERS.read_text()) if PLAYERS.exists() else {}
    assets = json.loads(ASSETS.read_text())
    rows = kept_calls(json.loads(CANDIDATES.read_text()), json.loads(VERDICTS.read_text()))
    before = ({r["id"]: r for r in json.loads(BOARD.read_text()).get("calls", [])}
              if BOARD.exists() else {})

    problems, board = [], []
    async with httpx.AsyncClient(timeout=20.0) as client:
        latest: dict[str, tuple[float, str] | None] = {}
        for row in sorted(rows, key=lambda r: (r["episode_id"], r["line"])):
            episode = shows.get(row["episode_id"])
            if episode is None:
                problems.append(f"{row['id']}: the show is not in data/episodes.json")
                continue
            ident = row["id"]
            row = placed(row, episode, labels.get(row["episode_id"], {}))
            if row is None:
                problems.append(f"{ident}: the quote is not on that line in that voice")
                continue
            asset = assets.get(row["symbol"])
            if not asset or not asset.get("yahoo") or not asset.get("name"):
                problems.append(f"{row['id']}: no listing on file for {row['symbol'] or row['asset']!r}")
                continue
            when = calls.aired(row["episode_id"], str(episode.get("published_at") or ""))
            if when is None:
                problems.append(f"{row['id']}: cannot tell when the show aired")
                continue
            listing, is_stock = asset["yahoo"], asset.get("class") in ("stock", "index", "commodity")

            old = before.get(row["id"], {})
            if old.get("listing") == listing and old.get("price_then"):
                then, then_at = old["price_then"], old["then_at"]
            elif args.offline:
                problems.append(f"{row['id']}: no price on file for {listing} and --offline was given")
                continue
            elif is_stock:
                meta, bars = await fetch(listing, when.date() - timedelta(days=6), when.date(), client)
                if not call_prices.is_listing(meta, listing, asset.get("answers_to") or asset["name"]):
                    problems.append(f"{row['id']}: {listing} answered as {meta.get('longName') or meta.get('shortName')!r}")
                    continue
                then = call_prices.close_by(bars, when)
                then_at = max((s for s, _, c in bars if s <= when and c), default=when).date().isoformat()
            else:
                mark = calls.daily_mark(when)
                meta, bars = await fetch(listing, mark - timedelta(days=2), mark, client)
                if not call_prices.is_listing(meta, listing, asset.get("answers_to") or asset["name"]):
                    problems.append(f"{row['id']}: {listing} answered as {meta.get('longName') or meta.get('shortName')!r}")
                    continue
                then, then_at = call_prices.at_mark(bars, mark), mark.isoformat()
            if not then:
                problems.append(f"{row['id']}: {listing} has no price for {when.date()}")
                continue

            if listing not in latest:
                if args.offline:
                    latest[listing] = ((old["price_last"], old["last_at"])
                                       if old.get("price_last") else None)
                else:
                    today = datetime.now(UTC).date()
                    _, bars = await fetch(listing, today - timedelta(days=6), today, client)
                    got = call_prices.latest(bars)
                    latest[listing] = (got[0], got[1].date().isoformat()) if got else None
            last = latest[listing]

            board.append({
                "id": row["id"], "episode_id": row["episode_id"],
                "show": " ".join(str(episode.get("title") or "").split()),
                "aired": when.isoformat(timespec="seconds"),
                "date": when.astimezone(HOME).date().isoformat(),
                "speaker": row["speaker"], "asset": asset["name"],
                "symbol": asset.get("ticker") or row["symbol"],
                "class": asset.get("class") or "crypto", "listing": listing,
                "stance": row["stance"], "kind": row["kind"],
                "target": row.get("target") or "", "quote": row["quote"],
                "start_seconds": row["start_seconds"],
                "timestamp": stamp(row["start_seconds"]),
                "link": link(episode, row["start_seconds"], players),
                "price_then": then, "then_at": then_at,
                "price_last": last[0] if last else None,
                "last_at": last[1] if last else None,
            })

    if problems:
        print("\n".join(f"  ! {p}" for p in problems))
        raise SystemExit(f"\n{len(problems)} problem(s). Nothing written.")
    board.sort(key=lambda r: (r["aired"], -r["start_seconds"]), reverse=True)
    priced_at = max((r["last_at"] for r in board if r["last_at"]), default=None)
    BOARD.write_text(json.dumps({
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "priced_at": priced_at, "calls": board}, indent=1, ensure_ascii=False) + "\n")
    told = calls.tally([dict(r, moved_pct=calls.moved(r["stance"], r["price_then"], r["price_last"]))
                        for r in board if r["kind"] != "exit"])
    print(f"{len(board)} calls -> {BOARD.relative_to(ROOT)}   {told}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

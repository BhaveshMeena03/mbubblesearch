"""Build the levels from the ones a person kept, and price them.

    .venv/bin/python scripts/build_levels.py            # build, price, check every level
    .venv/bin/python scripts/build_levels.py --offline  # rebuild rows, keep prices on file

Three files go in, all of them read by a person:

    data/levels_candidates.json  what scripts/extract_levels.py proposed
                                 and app/levels.py did not refuse
    data/levels_verdicts.json    id -> {"keep": true|false, "why": "..."}
                                 plus, on a kept row, corrections to
                                 "symbol", "kind" or "level", and "quote"
                                 or "line" for a better cut of the same
                                 moment. A level the model missed can be
                                 added with "by_hand".
    data/call_assets.json        ticker -> listing, shared with the calls
                                 board

One comes out, data/levels.json. Each level carries the price the hour
he said it (the last full hourly bar before that second) and where it
stands now: reached, and when, or how far is left. The daily check,
scripts/check_levels.py, refreshes that last part.

A level whose quote is no longer on its line, whose ticker has no
listing, or that sits implausibly far from the price when it was said
stops the build. Each is a wrong row caught before anybody saw it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_calls import HOME, episodes, fetch, link, stamp  # noqa: E402

from app import call_prices, calls, levels  # noqa: E402

DATA = ROOT / "data"
CANDIDATES = DATA / "levels_candidates.json"
VERDICTS = DATA / "levels_verdicts.json"
ASSETS = DATA / "call_assets.json"
OUT = DATA / "levels.json"
LABELS = DATA / "speaker_map.json"
PLAYERS = DATA / "broadcast_links.json"

CORRECTABLE = ("symbol", "kind", "level", "quiet", "by")
BY_HAND = ("episode_id", "line", "speaker", "symbol", "kind", "level", "quote")
# Hourly history goes back two years at the source, five-minute bars
# about sixty days. Past those, coarser.
HOURLY_DAYS = 720
FINE_DAYS = 55


def kept_levels(candidates: dict, verdicts: dict) -> list[dict]:
    """The candidates a person said keep, with their corrections, and the
    levels that person added. A kept verdict that points at nothing
    stops the build: the ids moved and rows would vanish quietly."""
    by_id = {c["id"]: c for show in candidates.get("shows", {}).values()
             for c in show.get("candidates", [])}
    stale = sorted(i for i, v in verdicts.items()
                   if v.get("keep") and not v.get("by_hand") and i not in by_id)
    if stale:
        raise SystemExit(f"{len(stale)} kept verdict(s) match no candidate, e.g. {stale[:3]}")
    rows = []
    for ident, verdict in verdicts.items():
        if not verdict.get("keep"):
            continue
        if verdict.get("by_hand"):
            missing = [f for f in BY_HAND if verdict.get(f) in (None, "")]
            if missing:
                raise SystemExit(f"{ident}: added by hand without {missing}")
            row = {"id": ident, "asset": "", "recut": True,
                   **{f: verdict[f] for f in BY_HAND}}
        else:
            row = dict(by_id[ident])
        for field in CORRECTABLE:
            if field in verdict:
                row[field] = verdict[field]
        if verdict.get("quote"):
            row["quote"], row["recut"] = verdict["quote"], True
        if "line" in verdict:
            row["line"], row["recut"] = int(verdict["line"]), True
        if row["kind"] not in levels.KINDS:
            raise SystemExit(f"{ident}: kind {row['kind']!r} is not one this knows")
        if row["speaker"] not in calls.HOSTS:
            raise SystemExit(f"{ident}: {row['speaker']!r} is not a host")
        row["symbol"] = str(row.get("symbol") or "").upper()
        row["level"] = float(row["level"])
        rows.append(row)
    return rows


def placed(row: dict, episode: dict, labels: dict[str, str]) -> dict | None:
    """The row as it stands in today's transcript, or None: the quote word
    for word on the line on file, in that host's voice, holding the
    number."""
    segments = episode.get("segments") or []
    span = calls.locate(row["quote"], segments, int(row["line"]))
    if span is None:
        return None
    if not row.get("recut") and span.first != int(row["line"]):
        return None
    if calls.voice(labels, span.first, span.last) != row["speaker"]:
        return None
    quote = calls.cut(segments, span)
    if not levels.said_in(row["level"], quote) or calls.tactical(quote):
        return None
    return dict(row, line=span.first, last_line=span.last, quote=quote,
                start_seconds=float(segments[span.first].get("t", 0.0)))


async def history(listing: str, since: datetime, client) -> list[levels.Bar]:
    """Bars from a day before `since` to today: hourly when the source
    keeps them that far back, daily before that."""
    today = datetime.now(UTC).date()
    hourly = (today - since.date()).days <= HOURLY_DAYS
    reply = await fetch(listing, since.date() - timedelta(days=1), today, client,
                        interval="1h" if hourly else "1d", raw=True)
    return levels.read_bars(reply)


async def check_all(rows: list[dict], client) -> dict[str, list[levels.Bar]]:
    """One history per listing, from the earliest level on it."""
    first: dict[str, datetime] = {}
    for row in rows:
        when = datetime.fromisoformat(row["said_at"])
        first[row["listing"]] = min(first.get(row["listing"], when), when)
    found = {}
    for listing, since in sorted(first.items()):
        found[listing] = await history(listing, since, client)
    return found


def refresh(rows: list[dict], bars_by: dict[str, list[levels.Bar]]) -> list[dict]:
    """Every row with where it stands today, from the bars on hand."""
    out = []
    for row in rows:
        bars = bars_by.get(row["listing"]) or []
        when = datetime.fromisoformat(row["said_at"])
        out.append(dict(row, check=levels.status(row["level"], row["price_then"], when, bars)))
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="no network: every price has to be on file already")
    args = ap.parse_args()

    shows = episodes()
    labels = json.loads(LABELS.read_text())
    players = json.loads(PLAYERS.read_text()) if PLAYERS.exists() else {}
    assets = json.loads(ASSETS.read_text())
    verdicts = json.loads(VERDICTS.read_text()) if VERDICTS.exists() else {}
    rows = kept_levels(json.loads(CANDIDATES.read_text()), verdicts)
    before = ({r["id"]: r for r in json.loads(OUT.read_text()).get("levels", [])}
              if OUT.exists() else {})

    problems, built = [], []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for row in sorted(rows, key=lambda r: r["id"]):
            ident = row["id"]
            episode = shows.get(row["episode_id"])
            if episode is None:
                problems.append(f"{ident}: the show is not in data/episodes.json")
                continue
            row = placed(row, episode, labels.get(row["episode_id"], {}))
            if row is None:
                problems.append(f"{ident}: the quote is not on that line in that voice, "
                                "or the number is not in it")
                continue
            aired = calls.aired(row["episode_id"], str(episode.get("published_at") or ""))
            if aired is None:
                problems.append(f"{ident}: cannot tell when the show aired")
                continue
            said_at = aired + timedelta(seconds=row["start_seconds"])
            asset = assets.get(row["symbol"])
            if not asset or not asset.get("yahoo") or not asset.get("name"):
                problems.append(f"{ident}: no listing on file for {row['symbol']!r}")
                continue
            listing = asset["yahoo"]
            old = before.get(ident, {})
            if old.get("listing") == listing and old.get("price_then"):
                then = old["price_then"]
            elif args.offline:
                problems.append(f"{ident}: no price on file and --offline was given")
                continue
            else:
                # Five-minute bars while the source still has them: a level
                # said in the middle of a crash ("wait for it to get back
                # above 2300") is on the wrong side of the hourly close.
                fine = (datetime.now(UTC) - said_at).days <= FINE_DAYS
                reply = await fetch(listing, said_at.date() - timedelta(days=1 if fine else 3),
                                    said_at.date(), client, interval="5m" if fine else "1h",
                                    raw=True)
                meta = call_prices.read_chart(reply)[0]
                if not call_prices.is_listing(meta, listing, asset.get("answers_to") or asset["name"]):
                    problems.append(f"{ident}: {listing} answered as "
                                    f"{meta.get('longName') or meta.get('shortName')!r}")
                    continue
                then = levels.price_at(levels.read_bars(reply), said_at,
                                       timedelta(minutes=5) if fine else timedelta(hours=1))
            if not then:
                problems.append(f"{ident}: {listing} has no price for {said_at:%Y-%m-%d %H:%M}")
                continue
            if not levels.sane(row["level"], then):
                problems.append(f"{ident}: {row['level']:g} is not a plausible level "
                                f"for {row['symbol']} at {then:g}")
                continue
            built.append({
                "id": ident, "episode_id": row["episode_id"],
                "show": " ".join(str(episode.get("title") or "").split()),
                "said_at": said_at.isoformat(timespec="seconds"),
                "date": said_at.astimezone(HOME).date().isoformat(),
                "speaker": row["speaker"], "asset": asset["name"],
                "symbol": asset.get("ticker") or row["symbol"],
                "class": asset.get("class") or "crypto", "listing": listing,
                "kind": row["kind"], "level": row["level"],
                "quote": row["quote"], "line": row["line"],
                "start_seconds": row["start_seconds"],
                "timestamp": stamp(row["start_seconds"]),
                "link": link(episode, row["start_seconds"], players),
                "price_then": then,
                "quiet": bool(row.get("quiet")), "by": row.get("by"),
                "check": old.get("check") if old.get("listing") == listing else None,
            })
        if problems:
            print("\n".join(f"  ! {p}" for p in problems))
            raise SystemExit(f"\n{len(problems)} problem(s). Nothing written.")
        if not args.offline:
            built = refresh(built, await check_all(built, client))

    built.sort(key=lambda r: (r["said_at"], r["id"]), reverse=True)
    OUT.write_text(json.dumps({
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "levels": built}, indent=1, ensure_ascii=False) + "\n")
    states = defaultdict(int)
    for r in built:
        states[(r.get("check") or {}).get("state", "unchecked")] += 1
    print(f"{len(built)} levels -> {OUT.relative_to(ROOT)}   {dict(states)}")
    for r in built:
        c = r.get("check") or {}
        print(f"  {r['date']} {r['speaker'][:5]:5} {r['symbol']:5} {r['kind']:6} "
              f"{r['level']:>10g}  then {r['price_then']:>10.2f}  {c.get('state', '?'):5} "
              f"{c.get('hit_at') or ''}{'' if c.get('hit_at') else str(c.get('away_pct')) + '% away'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

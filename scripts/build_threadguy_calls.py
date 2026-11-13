"""Build the ThreadGuy calls board (data/threadguy_calls.json).

    .venv/bin/python scripts/build_threadguy_calls.py            # fetch prices
    .venv/bin/python scripts/build_threadguy_calls.py --offline  # reuse prices on file

Same idea as build_calls.py. Inputs:
    data/threadguy_calls_candidates.json   from extract_threadguy_calls.py
    data/threadguy_calls_verdicts.json     keep/drop for every candidate
    data/call_assets.json                  yahoo listings

His streams have no speaker labels, so every kept row also needs where it
was checked on X: broadcast, x_second, on_air (broadcast start), checked.
An older stream that never aired on X can be kept with youtube: true,
on_air (when the live started), no_x (why) and checked; it links to YouTube.
Prices use the last full hour before he said it, not the daily close.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import call_prices, calls, local_cache  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_calls", ROOT / "scripts" / "build_calls.py")
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)

HOST = "ThreadGuy"
HOSTS = (HOST,)
DATA = ROOT / "data"
CANDIDATES = DATA / "threadguy_calls_candidates.json"
VERDICTS = DATA / "threadguy_calls_verdicts.json"
ASSETS = DATA / "call_assets.json"
BOARD = DATA / "threadguy_calls.json"
SHELF = DATA / "threadguy_index.json"
CHECKED = ("broadcast", "x_second", "on_air", "checked")
CHECKED_YOUTUBE = ("on_air", "no_x", "checked")
BY_HAND = ("episode_id", "line", "symbol", "stance", "kind", "quote")
_BROADCAST = re.compile(r"[A-Za-z0-9]{8,20}")
# the YouTube upload is often a day after the stream, so date rows by when he said it
HOME = ZoneInfo("America/New_York")


def kept_calls(candidates: dict, verdicts: dict) -> list[dict]:
    """Kept candidates with their corrections. Raises on bad verdicts."""
    by_id = {c["id"]: c for show in candidates.get("shows", {}).values()
             for c in show.get("candidates", [])}
    stale = sorted(i for i, v in verdicts.items()
                   if v.get("keep") and not v.get("by_hand") and i not in by_id)
    if stale:
        raise SystemExit(f"{len(stale)} kept verdict(s) match no candidate, e.g. {stale[:3]}. "
                         "Re-run scripts/extract_threadguy_calls.py or fix the verdicts.")
    rows = []
    for ident, verdict in verdicts.items():
        if not verdict.get("keep"):
            continue
        if verdict.get("by_hand"):
            missing = [f for f in BY_HAND if verdict.get(f) in (None, "")]
            if missing:
                raise SystemExit(f"{ident}: added by hand without {missing}")
            row = {"id": ident, "asset": "", "target": "", "recut": True, "speaker": HOST,
                   **{f: verdict[f] for f in BY_HAND}}
        else:
            row = dict(by_id[ident])
        for field in mb.CORRECTABLE:
            if field in verdict:
                row[field] = verdict[field]
        if verdict.get("quote"):
            row["quote"], row["recut"] = verdict["quote"], True
        if "line" in verdict:
            row["line"], row["recut"] = int(verdict["line"]), True
        if row["stance"] not in calls.STANCES or row["kind"] not in calls.KINDS:
            raise SystemExit(f"{ident}: stance/kind is not one the board knows")
        if row.get("speaker") != HOST:
            raise SystemExit(f"{ident}: {row.get('speaker')!r} is not {HOST}")
        if row["stance"] == "short" and not str(verdict.get("why") or "").strip():
            raise SystemExit(f"{ident}: a short needs a \"why\" saying it is a "
                             "view he holds and not a hedge or a trade")
        if verdict.get("youtube"):
            missing = [f for f in CHECKED_YOUTUBE if verdict.get(f) in (None, "")]
            if missing:
                raise SystemExit(f"{ident}: kept off X without {missing}")
            if verdict.get("broadcast"):
                raise SystemExit(f"{ident}: has an X broadcast, so it links there")
            row.update({f: verdict[f] for f in CHECKED_YOUTUBE}, youtube=True)
        else:
            missing = [f for f in CHECKED if verdict.get(f) in (None, "")]
            if missing:
                raise SystemExit(f"{ident}: kept without being checked on X ({missing})")
            if not _BROADCAST.fullmatch(str(verdict["broadcast"])):
                raise SystemExit(f"{ident}: {verdict['broadcast']!r} is not a broadcast id")
            row.update({f: verdict[f] for f in CHECKED})
        row["symbol"] = str(row.get("symbol") or "").upper()
        rows.append(row)
    return rows


def placed(row: dict, lines: list[dict]) -> dict | None:
    """The row checked against the transcript, or None if the quote moved or is a trade."""
    span = calls.locate(row["quote"], lines, row["line"])
    if span is None:
        return None
    if not row.get("recut") and span.first != row["line"]:
        return None
    said = calls.cut(lines, span)
    if calls.tactical(said):
        return None
    start = float(lines[span.first].get("t", 0.0))
    row = dict(row, line=span.first, last_line=span.last, quote=said, start_seconds=start)
    if row.get("youtube"):
        row["x_second"] = start     # no X copy, the YouTube second is the moment
    return row


def undash(line: str) -> str:
    """Same rule as the extension: a dash between words reads as a comma, between numbers a hyphen."""
    line = re.sub(r"(\d)\s*[\u2013\u2014]\s*(\d)", r"\1-\2", line)
    line = re.sub(r"\s*[\u2013\u2014]+\s*", ", ", line)
    line = re.sub(r"^,\s*|,\s*$", "", line)
    return re.sub(r",\s*([,.;:!?])", r"\1", line)


def close_before(bars: list[call_prices.Bar], when: datetime,
                 width: timedelta = timedelta(hours=1)) -> float | None:
    """Close of the last hour bar that ended before `when` (max 6 days old,
    so easter's thursday-to-tuesday closure still has thursday's close).
    The bar he was talking in closes after him, so it can't be used."""
    done = [(start, closed) for start, _, closed in bars
            if start + width <= when and closed is not None]
    if not done:
        return None
    start, closed = max(done)
    return closed if when - start <= timedelta(days=6) else None


def said_at(row: dict) -> datetime:
    began = datetime.fromisoformat(str(row["on_air"]).replace("Z", "+00:00"))
    return began + timedelta(seconds=float(row["x_second"]))


def link(row: dict) -> str:
    # start 1s early so the first word isn't cut off
    at = max(0, int(row["x_second"]) - 1)
    if row.get("youtube"):
        return f"https://www.youtube.com/watch?v={row['episode_id']}&t={at}s"
    return f"https://x.com/i/broadcasts/{row['broadcast']}?t={at}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="no network: every price has to be on file already")
    args = ap.parse_args()

    shelf = {r["id"]: r for r in json.loads(SHELF.read_text())}
    assets = json.loads(ASSETS.read_text())
    rows = kept_calls(json.loads(CANDIDATES.read_text()), json.loads(VERDICTS.read_text()))
    before = ({r["id"]: r for r in json.loads(BOARD.read_text()).get("calls", [])}
              if BOARD.exists() else {})
    today = datetime.now(UTC).date()

    problems, board = [], []
    async with httpx.AsyncClient(timeout=20.0) as client:
        latest: dict[str, tuple[float, str] | None] = {}

        async def newest(listing: str, old: dict) -> tuple[float, str] | None:
            if listing not in latest:
                if args.offline:
                    latest[listing] = ((old["price_last"], old["last_at"])
                                       if old.get("price_last") else None)
                else:
                    _, bars = await mb.fetch(listing, today - timedelta(days=6), today, client)
                    got = call_prices.latest(bars)
                    latest[listing] = (got[0], got[1].date().isoformat()) if got else None
            return latest[listing]

        async def checked(row: dict, listing: str, then_at: str, old: dict) -> dict | None:
            level = row.get("level")
            if level is None or row["stance"] != "long":
                return None
            was = old.get("target_check") or {}
            if args.offline:
                same = was.get("level") == float(level) and was.get("by") == row.get("by")
                return was if same else None
            reply = await mb.fetch(listing, datetime.fromisoformat(then_at).date(), today,
                                   client, raw=True)
            return calls.target_status(level, row.get("by"),
                                       call_prices.read_highs(reply), today)

        for row in sorted(rows, key=lambda r: r["id"]):
            ident = row["id"]
            stream = shelf.get(row["episode_id"])
            if stream is None:
                problems.append(f"{ident}: the stream is not on the ThreadGuy shelf")
                continue
            lines = local_cache.load_lines("threadguy", row["episode_id"]) or []
            if not lines:
                problems.append(f"{ident}: no transcript on this laptop for {row['episode_id']}")
                continue
            row = placed(row, lines)
            if row is None:
                problems.append(f"{ident}: the quote is not on that line, "
                                "or it describes a hedge or a trade")
                continue
            asset = assets.get(row["symbol"])
            if not asset or not asset.get("yahoo") or not asset.get("name"):
                problems.append(f"{ident}: no listing on file for {row['symbol'] or row['asset']!r}")
                continue
            listing = asset["yahoo"]
            when = said_at(row)

            old = before.get(ident, {})
            if old.get("listing") == listing and old.get("price_then") and old.get("aired") == \
                    when.isoformat(timespec="seconds"):
                then, then_at = old["price_then"], old["then_at"]
            elif args.offline:
                problems.append(f"{ident}: no price on file for {listing} and --offline was given")
                continue
            else:
                meta, bars = await mb.fetch(listing, when.date() - timedelta(days=7),
                                            when.date(), client, interval="1h")
                if not call_prices.is_listing(meta, listing, asset.get("answers_to") or asset["name"]):
                    problems.append(f"{ident}: {listing} answered as "
                                    f"{meta.get('longName') or meta.get('shortName')!r}")
                    continue
                then, then_at = close_before(bars, when), when.date().isoformat()
            if not then:
                problems.append(f"{ident}: {listing} has no price before {when.isoformat()}")
                continue

            last = await newest(listing, old)
            check = await checked(row, listing, then_at, old)
            if row.get("level") is not None and check is None:
                problems.append(f"{ident}: the target has a level and could not be checked")
                continue

            board.append({
                "id": ident, "source": "show", "episode_id": row["episode_id"],
                "show": " ".join(str(stream.get("title") or "").split()),
                "aired": when.isoformat(timespec="seconds"),
                "date": when.astimezone(HOME).date().isoformat(),
                "speaker": HOST, "asset": asset["name"],
                "symbol": asset.get("ticker") or row["symbol"],
                "class": asset.get("class") or "crypto", "listing": listing,
                "stance": row["stance"], "kind": row["kind"],
                "target": row.get("target") or "", "quote": undash(row["quote"]),
                # second the link opens at, transcript line/second for the tests
                "start_seconds": float(row["x_second"]),
                "line": row["line"], "youtube_seconds": row["start_seconds"],
                "timestamp": mb.stamp(row["x_second"]),
                "link": link(row),
                "price_then": then, "then_at": then_at,
                "price_last": last[0] if last else None,
                "last_at": last[1] if last else None,
                "target_check": check,
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

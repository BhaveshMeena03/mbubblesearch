"""Match the ticker readings to Polymarket's markets, on a GitHub runner.

    python scripts/polymarket_match.py --out polymarket_board.json

Polymarket is blocked from India, where the laptop is, so this runs in
.github/workflows/polymarket.yml on a US runner and the board it writes
comes back as an artifact. Nothing here pushes: the board is read by a
person and committed from the laptop like every other shipped file.

For each market the ticker showed (data/polymarket/ticker/*.json):

  1. search Polymarket for what the ticker said;
  2. keep the candidates that are the same question by app/polymarket.py's
     rules (every figure and month equal, nearly letter for letter) and
     that were open when it was on screen;
  3. fetch each one's minute by minute price for the length of the show,
     and keep the one whose price agreed with the ticker's odds, within
     two points, on at least eight in ten of the times it was on screen.

Anything that does not come down to exactly one market is written with
status "review" and the reason, never guessed. The board also records each
market's standing now (resolved yes, no, closed or open at a price), which
the site keeps fresh on its own.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import polymarket as pm  # noqa: E402

TICKER = ROOT / "data" / "polymarket" / "ticker"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HEADERS = {"User-Agent": "marketbubblesearch/1.0 (+https://search.lexthedev.com)",
           "Accept": "application/json"}
# Polite spacing between requests; the whole run is a few hundred.
PAUSE = 0.15
# How much price history either side of the show is fetched.
MARGIN = timedelta(minutes=10)


class Polymarket:
    """The three public endpoints this needs, with retries and a cache."""

    def __init__(self, record: Path | None = None) -> None:
        self.cache: dict[str, object] = {}
        self.calls = 0
        self.record = record
        if record:
            record.mkdir(parents=True, exist_ok=True)

    def get(self, url: str):
        if url in self.cache:
            return self.cache[url]
        last = None
        for attempt in range(4):
            time.sleep(PAUSE)
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = json.loads(r.read())
                break
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (429, 500, 502, 503, 504):
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
                last = exc
            time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"Polymarket did not answer {url}: {last}")
        self.cache[url] = body
        if self.record:
            name = f"{len(self.cache):04d}.json"
            (self.record / name).write_text(json.dumps({"url": url, "body": body}))
        return body

    def search(self, text: str) -> list[tuple[dict, dict]]:
        """(event, market) for every market in the events a search finds."""
        q = urllib.parse.urlencode({"q": text, "limit_per_type": 20})
        found = self.get(f"{GAMMA}/public-search?{q}") or {}
        out = []
        for event in found.get("events") or []:
            for market in event.get("markets") or []:
                out.append((event, market))
        return out

    def history(self, token: str, start: datetime, end: datetime) -> list[dict]:
        q = urllib.parse.urlencode({"market": token, "startTs": int(start.timestamp()),
                                    "endTs": int(end.timestamp()), "fidelity": 1})
        got = self.get(f"{CLOB}/prices-history?{q}") or {}
        return got.get("history") or []

    def markets(self, ids: list[str]) -> dict[str, dict]:
        """The current state of markets, fifty at a time.

        Asked twice, because the endpoint leaves closed markets out unless
        told to include them, and leaves open ones out when it is (seen on
        10 Nov 2026: two ids asked for, the closed one silently missing).
        """
        out = {}
        for i in range(0, len(ids), 50):
            chunk = [("id", x) for x in ids[i:i + 50]]
            for extra in ([], [("closed", "true")]):
                q = urllib.parse.urlencode(chunk + extra)
                for market in self.get(f"{GAMMA}/markets?{q}") or []:
                    out[str(market.get("id"))] = market
        missing = set(ids) - set(out)
        for mid in sorted(missing):
            out[mid] = self.get(f"{GAMMA}/markets/{mid}")
        return out


def queries(text: str) -> list[str]:
    """What to search for: the reading, then the reading without its
    question word, because search ranks on the rare words. Junk the icon
    left in front without a single letter in it ("%2 NATO x Russia...") is
    cut first: search found nothing for it."""
    base = re.sub(r"^(?:[^A-Za-z\s]+\s+)+", "", text.rstrip("?").strip())
    out = [base]
    words = base.split()
    if words and pm.squash(words[0]) in {"will", "willthe", "vill"}:
        rest = " ".join(words[1:])
        if rest.lower().startswith("the "):
            rest = rest[4:]
        out.append(rest)
    return out


def sightings_of(market: dict) -> list[pm.Sighting]:
    return [pm.Sighting(t=t, aired=datetime.fromisoformat(aired), yes=yes,
                        text=text, whole=whole)
            for t, aired, yes, whole, text in market["sightings"]]


def match_one(api: Polymarket, shown: dict, start: datetime, end: datetime) -> dict:
    """One market off the ticker, decided."""
    seen = sightings_of(shown)
    first = min(seen, key=lambda s: s.t)
    text = shown["text"]
    whole = any(s.whole for s in seen)
    considered, passed = [], []
    candidates: dict[str, tuple[dict, dict]] = {}
    for q in queries(text):
        for event, market in api.search(q):
            candidates.setdefault(str(market.get("id")), (event, market))
    for mid, (event, market) in candidates.items():
        question = market.get("question") or ""
        ok, why = pm.same_question(text, question, whole=whole)
        if not ok:
            continue
        if not pm.open_at(market, first.aired):
            considered.append({"id": mid, "question": question, "why": "not open then"})
            continue
        token = pm.yes_token(market)
        if not token:
            considered.append({"id": mid, "question": question, "why": "no yes token"})
            continue
        history = api.history(token, start - MARGIN, end + MARGIN)
        agreed = pm.agreement(seen, history)
        entry = {"id": mid, "question": question, "text_check": why,
                 "agree": agreed.agree, "strict": agreed.strict,
                 "compared": agreed.compared, "worst": agreed.worst}
        considered.append(entry)
        if agreed.holds:
            passed.append((agreed, event, market, history))

    row = {
        "ocr": text,
        "readings": len(seen),
        "first": {"t": first.t, "aired": first.aired.isoformat(), "yes": first.yes},
        "last": {"t": max(s.t for s in seen),
                 "aired": max(s.aired for s in seen).isoformat()},
        "considered": considered,
    }
    if len(passed) == 1 or (len(passed) > 1 and _clear_winner(passed)):
        agreed, event, market, history = max(
            passed, key=lambda p: (p[0].share, p[0].strict_share, p[0].compared))
        # The odds the page shows are from the first sighting the market
        # bears out at its own minute, never a misread or a lagging one: on
        # 5 Nov the first reading of the GTA VI market said 81% when the
        # market was at 74, and on 29 Oct the ticker showed Ethereum at 17%
        # minutes after the market had fallen to 8. When the market was
        # first seen is kept apart, from the earliest sighting of any kind.
        shown = min((s for s in seen if pm.agrees(s, history, at_minute=True)),
                    key=lambda s: s.t)
        then = pm.price_at(history, shown.aired)
        row["seen_from"] = {"t": first.t, "aired": first.aired.isoformat()}
        row["first"] = {"t": shown.t, "aired": shown.aired.isoformat(), "yes": shown.yes}
        row.update({
            "status": "matched",
            "market": {"id": str(market["id"]), "question": market.get("question"),
                       "slug": market.get("slug"), "event_slug": event.get("slug"),
                       "event_title": event.get("title"),
                       "url": f"https://polymarket.com/event/{event.get('slug')}",
                       "yes_token": pm.yes_token(market),
                       "opened": market.get("startDate"), "ends": market.get("endDate")},
            "price_then": None if then is None else round(then * 100, 1),
            "agreement": {"agree": agreed.agree, "strict": agreed.strict,
                          "compared": agreed.compared, "worst": agreed.worst},
        })
    else:
        row["status"] = "review"
        row["why"] = ("no market matched" if not considered else
                      "the odds agreed with more than one market" if len(passed) > 1 else
                      "a market matched the words but not the odds")
    return row


def _clear_winner(passed: list) -> bool:
    """Two markets both agreeing is settled only when one agreed on clearly
    more sightings, or, agreeing as often, clearly more of them at their own
    minute; otherwise a person decides."""
    ranked = sorted(passed, key=lambda p: (p[0].share, p[0].strict_share), reverse=True)
    a, b = ranked[0][0], ranked[1][0]
    return a.share - b.share >= 0.2 or (
        a.share - b.share < 0.2 and a.strict_share - b.strict_share >= 0.2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=Path("polymarket_board.json"))
    ap.add_argument("--record", type=Path, default=None,
                    help="also save every API response here, for the tests")
    args = ap.parse_args()

    api = Polymarket(record=args.record)
    episodes = []
    for path in sorted(TICKER.glob("*.json")):
        ep = json.loads(path.read_text())
        start, end = (datetime.fromisoformat(ep["aired"]), datetime.fromisoformat(ep["ended"]))
        rows = []
        for shown in ep["markets"]:
            row = match_one(api, shown, start, end)
            key_id = row["market"]["id"] if row["status"] == "matched" else f"?{len(rows)}"
            row["key"] = f"{ep['episode_id']}:{key_id}"
            rows.append(row)
        # The same market caught under two readings is one row: the
        # earlier sighting stands, the later one's sightings are counted in.
        merged: dict[str, dict] = {}
        for row in rows:
            if row["key"] in merged:
                kept = merged[row["key"]]
                kept["readings"] += row["readings"]
                if row["first"]["t"] < kept["first"]["t"]:
                    kept["first"] = row["first"]
                if row["last"]["t"] > kept["last"]["t"]:
                    kept["last"] = row["last"]
                continue
            merged[row["key"]] = row
        episodes.append({
            "episode_id": ep["episode_id"], "title": ep.get("title"),
            "broadcast": ep["broadcast"], "aired": ep["aired"], "ended": ep["ended"],
            "rows": list(merged.values()),
        })
        matched = sum(r["status"] == "matched" for r in merged.values())
        print(f"{ep['episode_id']}: {matched} matched, {len(merged) - matched} for review")

    ids = sorted({r["market"]["id"] for e in episodes for r in e["rows"]
                  if r["status"] == "matched"})
    now = api.markets(ids)
    for e in episodes:
        for r in e["rows"]:
            if r["status"] == "matched" and r["market"]["id"] in now:
                r["standing"] = pm.outcome(now[r["market"]["id"]])
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    args.out.write_text(json.dumps({"episodes": episodes, "built_at": stamp,
                                    "checked_at": stamp}, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {args.out} after {api.calls} requests")
    for e in episodes:
        print(f"\n## {e['title']} ({e['aired'][:10]})")
        for r in e["rows"]:
            if r["status"] == "matched":
                a = r["agreement"]
                print(f"  ok  {r['first']['yes']:>3}% shown, {r['price_then']}% then, "
                      f"{a['agree']}/{a['compared']} agree, {a['strict']} at the minute "
                      f"(worst {a['worst']}) | "
                      f"{r['market']['question']} | {r.get('standing')}")
            else:
                print(f"  ??  {r['why']} | {r['ocr']} | {r['considered'][:3]}")


if __name__ == "__main__":
    main()

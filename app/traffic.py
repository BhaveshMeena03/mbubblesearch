"""Usage that survives a deploy.

/v1/stats counted from zero every time the service restarted, and it
restarts on every push, so on 2026-10-02 it could say "5 searches since
16:47 yesterday" and nothing about the day before. The ANALYTICS log lines
were the durable record, which in practice meant there was none: nobody
greps a host's logs to find out whether a post brought anyone in.

This keeps one small record per UTC day in Pinecone, which the service
already reads and writes, in a namespace of its own:

    counts     every _track() event: searches per archive, summaries, clips
    pages      page views per path (people, not link-preview bots)
    referrers  where those views came from: t.co is a post on X
    visitors   distinct visitors that day, as salted hashes

Visitors are hashed with the day in the salt, so the same person is one
visitor per day and cannot be followed from one day to the next, and no
address is ever stored.

Writes go out every few minutes and at shutdown. Each one re-reads the
stored day and adds what this process saw since its last write, rather than
overwriting with its own total: during a deploy two instances run at once,
and a plain overwrite would let the old one erase the new one's morning.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from collections import Counter
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

NAMESPACE = "traffic"
FLUSH_SECONDS = 300
HISTORY_DAYS = 30
# Pinecone caps metadata at 40KB a record. 2,000 ten-character hashes is
# about 26KB; past that the day keeps counting visitors without their hashes.
MAX_VISITOR_HASHES = 2_000

# Pages a person opens. API calls are counted by what they do, through
# _track, not here.
PAGES = re.compile(r"^/(?:home|mcg|elon|musk|finance|record|threadguy|method"
                   r"|threadguy/(?:tokens|assets)|mcg/(?:assets|tokens)"
                   r"|demo/[\w-]+\.html)?/?$")
# Link previews fetch the page too. Counting X's card fetcher as a visitor
# would make every post look like it brought people in.
BOTS = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|embedly"
                  r"|whatsapp|telegram|discord|curl|python-requests|httpx|wget"
                  r"|headless", re.I)


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def visitor_hash(client: str, day: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{day}|{client}".encode()).hexdigest()[:10]


def referrer_host(referer: str | None) -> str:
    if not referer:
        return "direct"
    host = (urlparse(referer).hostname or "").lower().removeprefix("www.")
    return host or "direct"


def empty_day() -> dict:
    return {"counts": Counter(), "pages": Counter(), "referrers": Counter(),
            "visitors": set(), "visitor_overflow": 0, "bot_views": 0}


def merge(a: dict, b: dict) -> dict:
    """Two partial days, added together."""
    out = empty_day()
    for part in (a, b):
        out["counts"].update(part.get("counts", {}))
        out["pages"].update(part.get("pages", {}))
        out["referrers"].update(part.get("referrers", {}))
        out["visitors"] |= set(part.get("visitors", ()))
        out["visitor_overflow"] += int(part.get("visitor_overflow", 0))
        out["bot_views"] += int(part.get("bot_views", 0))
    if len(out["visitors"]) > MAX_VISITOR_HASHES:
        extra = sorted(out["visitors"])[MAX_VISITOR_HASHES:]
        out["visitors"] -= set(extra)
        out["visitor_overflow"] += len(extra)
    return out


def to_metadata(day: str, data: dict) -> dict:
    return {"day": day,
            "counts": json.dumps(dict(data["counts"])),
            "pages": json.dumps(dict(data["pages"])),
            "referrers": json.dumps(dict(data["referrers"])),
            "visitors": json.dumps(sorted(data["visitors"])),
            "visitor_overflow": int(data["visitor_overflow"]),
            "bot_views": int(data["bot_views"])}


def from_metadata(meta: dict | None) -> dict:
    if not meta:
        return empty_day()

    def load(key: str, default):
        try:
            return json.loads(meta.get(key) or "null") or default
        except (TypeError, ValueError):
            return default
    return {"counts": Counter(load("counts", {})), "pages": Counter(load("pages", {})),
            "referrers": Counter(load("referrers", {})),
            "visitors": set(load("visitors", [])),
            "visitor_overflow": int(meta.get("visitor_overflow") or 0),
            "bot_views": int(meta.get("bot_views") or 0)}


def day_summary(day: str, data: dict) -> dict:
    counts = data["counts"]
    searches = sum(n for k, n in counts.items() if k.endswith("_searches"))
    return {"day": day,
            "visitors": len(data["visitors"]) + data["visitor_overflow"],
            "page_views": sum(data["pages"].values()),
            "searches": searches,
            "bot_views": data["bot_views"],
            "top_pages": dict(data["pages"].most_common(6)),
            "referrers": dict(data["referrers"].most_common(6)),
            "events": dict(counts)}


class Traffic:
    """The day's counts in memory, written to Pinecone every few minutes."""

    def __init__(self, index=None, salt: str = "", namespace: str = NAMESPACE,
                 dimension: int = 1024, index_factory=None) -> None:
        self._index = index
        self._index_factory = index_factory
        self._salt = salt
        self._namespace = namespace
        self._dimension = dimension
        self._pending: dict[str, dict] = {}
        self._stored: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # -- recording, called on the request path: memory only, never I/O ----

    def _day(self) -> dict:
        return self._pending.setdefault(today(), empty_day())

    def event(self, kind: str) -> None:
        self._day()["counts"][kind] += 1

    def page(self, path: str, client: str, user_agent: str | None,
             referer: str | None) -> None:
        if not PAGES.match(path):
            return
        day = self._day()
        if BOTS.search(user_agent or "") or not user_agent:
            day["bot_views"] += 1
            return
        day["pages"][path.rstrip("/") or "/"] += 1
        day["referrers"][referrer_host(referer)] += 1
        day["visitors"].add(visitor_hash(client, today(), self._salt))

    # -- storage ---------------------------------------------------------

    def connect(self, index_factory, salt: str, dimension: int) -> None:
        """Called once at startup. The index is built on first use, in a
        worker thread, so startup never waits on Pinecone."""
        self._index_factory = index_factory
        self._salt = salt
        self._dimension = dimension

    @property
    def ready(self) -> bool:
        return self._index is not None or self._index_factory is not None

    def _idx(self):
        if self._index is None and self._index_factory is not None:
            self._index = self._index_factory()
        return self._index

    def _vector(self, day: str, data: dict) -> dict:
        values = [0.0] * self._dimension
        values[0] = 1.0                  # never similarity-searched
        return {"id": f"traffic-{day}", "values": values,
                "metadata": to_metadata(day, data)}

    def _fetch(self, days: list[str]) -> dict[str, dict]:
        got = self._idx().fetch(ids=[f"traffic-{d}" for d in days],
                                namespace=self._namespace)
        return {v.metadata.get("day"): from_metadata(v.metadata)
                for v in got.vectors.values() if v.metadata}

    async def load(self, days: int = HISTORY_DAYS) -> None:
        """Read the stored history, so the stats page starts where the last
        deploy left off rather than at zero."""
        if not self.ready:
            return
        wanted = [time.strftime("%Y-%m-%d", time.gmtime(time.time() - i * 86_400))
                  for i in range(days)]
        try:
            self._stored = await asyncio.to_thread(self._fetch, wanted)
        except Exception as exc:                                # noqa: BLE001
            logger.warning("traffic: could not load history: %s", exc)

    async def flush(self) -> None:
        """Add what this process saw to what is stored, and write it."""
        if not self.ready or not self._pending:
            return
        async with self._lock:
            pending, self._pending = self._pending, {}
            try:
                stored = await asyncio.to_thread(self._fetch, list(pending))
                merged = {d: merge(stored.get(d, empty_day()), pending[d])
                          for d in pending}
                await asyncio.to_thread(
                    lambda vectors: self._idx().upsert(vectors=vectors,
                                                       namespace=self._namespace),
                    [self._vector(d, m) for d, m in merged.items()])
                self._stored.update(merged)
            except Exception as exc:                            # noqa: BLE001
                # Put it back: the next flush tries again, nothing is lost.
                for d, data in pending.items():
                    self._pending[d] = merge(data, self._pending.get(d, empty_day()))
                logger.warning("traffic: flush failed, will retry: %s", exc)

    async def run(self, every: float = FLUSH_SECONDS) -> None:
        while True:
            await asyncio.sleep(every)
            await self.flush()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.flush()

    # -- reading ---------------------------------------------------------

    def history(self, days: int = 14) -> list[dict]:
        all_days = set(self._stored) | set(self._pending)
        rows = []
        for day in sorted(all_days, reverse=True)[:days]:
            data = merge(self._stored.get(day, empty_day()),
                         self._pending.get(day, empty_day()))
            rows.append(day_summary(day, data))
        return rows

    def totals(self) -> dict:
        rows = self.history(days=HISTORY_DAYS)
        # Daily uniques added up: someone who came on three days counts
        # three times, so it is visitor-days, not people.
        return {"since": rows[-1]["day"] if rows else today(),
                "days": len(rows),
                "visitor_days": sum(r["visitors"] for r in rows),
                "page_views": sum(r["page_views"] for r in rows),
                "searches": sum(r["searches"] for r in rows)}

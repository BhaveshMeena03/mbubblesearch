"""How much this process has read out of Pinecone, in bytes.

Pinecone bills reads by the byte against a monthly cap, and the only
place that total is shown is a dashboard page that nothing here can read.
On 19 Oct 2026 the cap was found the way a cap is found when nobody is
counting: every search on five archives returned a bare 500. So every
Index this code constructs goes through `metered`, which weighs what each
query, fetch and list brings back and keeps a running total, by day. It
is a count of this process's reads, not the project's, which is the
morning job on the laptop and the service on Render added together; both
print theirs, and /v1/stats carries the service's.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# Said once per 100 MB on a day, so a runaway reader shows up in the log
# before it shows up on the invoice.
STEP = 100 * 1024 * 1024

_lock = threading.Lock()
_by_day: dict[str, int] = defaultdict(int)
_by_kind: dict[str, int] = defaultdict(int)
_calls: dict[str, int] = defaultdict(int)
_said: dict[str, int] = defaultdict(int)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _weigh(response) -> int:
    """The response as the wire carried it, near enough: its JSON."""
    try:
        if hasattr(response, "to_dict"):
            return len(json.dumps(response.to_dict()))
        return len(json.dumps(response))
    except Exception:                                    # noqa: BLE001
        return 0


def record(kind: str, size: int) -> None:
    day = _today()
    with _lock:
        _by_day[day] += size
        _by_kind[kind] += size
        _calls[kind] += 1
        total = _by_day[day]
        crossed = total // STEP
        if crossed > _said[day]:
            _said[day] = crossed
            logger.warning("pinecone reads today: %d MB (%s)",
                           total // (1024 * 1024), kind)


def snapshot() -> dict:
    """For /v1/stats and the end of a script."""
    with _lock:
        return {
            "today_mb": round(_by_day[_today()] / (1024 * 1024), 2),
            "since_start_mb": round(sum(_by_day.values()) / (1024 * 1024), 2),
            "by_kind_mb": {k: round(v / (1024 * 1024), 2)
                           for k, v in sorted(_by_kind.items())},
            "calls": dict(_calls),
        }


def metered(index, label: str = ""):
    """The same Index, with its reads counted. Query and fetch responses are
    weighed whole; a list only carries ids, so it is counted by the id."""
    if getattr(index, "_egress_metered", False):
        return index
    real_query, real_fetch, real_list = index.query, index.fetch, index.list
    tag = f"{label}:" if label else ""

    def query(*args, **kwargs):
        response = real_query(*args, **kwargs)
        record(f"{tag}query:{kwargs.get('namespace', '')}", _weigh(response))
        return response

    def fetch(*args, **kwargs):
        response = real_fetch(*args, **kwargs)
        record(f"{tag}fetch:{kwargs.get('namespace', '')}", _weigh(response))
        return response

    def listed(*args, **kwargs):
        for page in real_list(*args, **kwargs):
            items = getattr(page, "vectors", None) or page
            try:
                n = len(items)
            except TypeError:
                n = 0
            record(f"{tag}list:{kwargs.get('namespace', '')}", n * 64)
            yield page

    index.query, index.fetch, index.list = query, fetch, listed
    index._egress_metered = True
    return index

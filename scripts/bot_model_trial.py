"""Which model should the X bot answer with? Measured in the bot's own voice.

    env -u ANTHROPIC_BASE_URL SEARCH_MODEL=claude-opus-5 \\
        .venv/bin/python scripts/bot_model_trial.py --rule --out /tmp/c.json

The question bank grades the website's answers. The bot answers with
reply_style() added to the prompt, and that instruction pushes toward
answering -- "if the question is broad, pick the most striking thing" --
which is harmless on Haiku, a cautious model, and not on Opus: on the bank,
Opus offered "the closest thing is..." for 14 of 21 topics the show never
covered, where Haiku declined 19. A bot that does that replies in public
to questions it should leave alone.

So this asks two sets: the 21 absent topics, where declining is the pass,
and 40 answerable questions, where declining is the failure. A rule that
fixes the first by making the model timid on the second fixes nothing.
--rule adds the not-there rule under test; without it, the bot's current
instruction is used as is.

UsePod only: refuses to run if model calls would go anywhere else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import hundred_questions as bank  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from app.x_bot import format_reply, reply_style  # noqa: E402

# The rule under test. It has to outrank two lines of reply_style that
# point the other way ("pick the most striking thing", "do not open by
# saying what you could not find"), so it says so.
NOT_THERE = """
- If nothing in the excerpts is actually about what was asked, reply with \
exactly: "I couldn't find that in the episodes I've indexed." Nothing else. \
Do not offer the closest related moment, a different topic, or a guess at \
what they meant: a related moment is not an answer, and posting one in \
public replies to a question the show never covered. This overrides the \
rules above about leading with an answer, which apply only when the \
excerpts DO cover the question. Broad means the excerpts cover the topic \
in many places, so pick the best one; absent means they do not cover it, \
so say so."""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", action="store_true", help="add the not-there rule")
    ap.add_argument("--answerable", type=int, default=40)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    settings = get_settings()
    host = urlparse(settings.anthropic_base_url or "").hostname or ""
    if host != "api.usepod.ai":
        print(f"  refusing to run: calls would go to {host or 'Anthropic'}, "
              f"not UsePod. Run it as: env -u ANTHROPIC_BASE_URL ...")
        return 2

    instruction = reply_style(settings.x_bot_post_limit)
    if args.rule:
        instruction += NOT_THERE

    random.seed(20260919)          # the same forty for every condition
    answerable = random.sample(bank.RECALL + bank.VAGUE + bank.GUEST,
                               args.answerable)
    questions = ([("absent", q) for q in bank.ABSENT]
                 + [("answerable", q) for q in answerable])
    episodes = {e["episode_id"]: e for e in json.loads(bank.EPISODES.read_text())}

    index, rows = PodcastIndex(), []
    for n, (kind, question) in enumerate(questions, 1):
        started = time.time()
        try:
            result = await index.search(question, instruction=instruction)
        except Exception as exc:                                # noqa: BLE001
            rows.append({"kind": kind, "q": question, "verdict": "ERROR",
                         "detail": type(exc).__name__})
            print(f"  {n:2}. ERROR    {question[:56]}")
            continue
        took = round(time.time() - started, 1)
        answer = result.answer or ""
        reply = format_reply(answer, result.hits, include_links="always",
                             limit=settings.x_bot_post_limit)
        declined = bool(bank.DECLINED.search(answer)) or not reply
        if kind == "absent":
            verdict = "pass" if declined else "ANSWERED"
        elif declined:
            verdict = "miss"
        else:
            problem = bank.quotes_hold(answer, episodes, result.hits)
            verdict = "UNSUPPORTED" if problem else "ok"
        rows.append({"kind": kind, "q": question, "verdict": verdict,
                     "seconds": took, "answer": answer[:500]})
        print(f"  {n:2}. {verdict:11} {took:5.1f}s  {question[:56]}")

    args.out.write_text(json.dumps(rows, indent=1))
    for kind in ("absent", "answerable"):
        mine = [r for r in rows if r["kind"] == kind]
        good = sum(r["verdict"] in ("pass", "ok") for r in mine)
        print(f"\n  {kind:10} {good}/{len(mine)}")
    took = sorted(r["seconds"] for r in rows if "seconds" in r)
    if took:
        print(f"  latency: median {took[len(took) // 2]:.1f}s · "
              f"p90 {took[int(len(took) * 0.9)]:.1f}s · slowest {took[-1]:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

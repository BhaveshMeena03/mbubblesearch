"""Check the voice labels in the Musk archive against what the words say.

    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/check_elon_voices.py
    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/check_elon_voices.py --each 200

label_elon_voices.py names a line from its sound. This asks a second
witness that never heard it: a model is shown a stretch of transcript with
no names on it and one line marked, and asked whether the guest or
somebody else is speaking. Two methods that share nothing agreeing on a
line is evidence; disagreeing is a line worth reading.

It is not a referee. A reader of bare text is wrong on its own often
enough (a question put as a statement, a host finishing the guest's
sentence), so every disagreement is printed and the number that matters
is the one a person gets after reading them. What it catches cheaply is
the systematic fault: a whole voice given the wrong name, or lines that
begin in one mouth and end in another.

Lines of ten words or more only. "Yeah." cannot be attributed from text,
and is not what anybody quotes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anthropic import AsyncAnthropic  # noqa: E402

from app.config import anthropic_client_kwargs, get_settings, redact  # noqa: E402

EPISODES = ROOT / "data" / "elon_episodes.json"
MAP = ROOT / "data" / "elon_speaker_map.json"
ELON = "Elon Musk"
BEFORE, AFTER, WORDS, PER_CALL = 5, 3, 10, 8

SYSTEM = (
    "You are shown short stretches of an interview transcript with no "
    "speaker names. The guest is Elon Musk. Everyone else is an "
    "interviewer, a host or another guest. In each stretch one line is "
    "marked with >>. Decide who is speaking THAT line, from the "
    "conversation around it: G if it is Elon Musk, O if it is anybody "
    "else, U if the text does not let you tell. Answer with a JSON list "
    "of the letters, one per stretch, in order, and nothing else."
)


def sample(each: int, seed: int) -> list[dict]:
    episodes = {e["episode_id"]: e for e in json.loads(EPISODES.read_text())}
    labelled = json.loads(MAP.read_text())
    his, theirs = [], []
    for vid, names in labelled.items():
        segments = episodes[vid]["segments"]
        for index, who in names.items():
            i = int(index)
            if len(segments[i]["text"].split()) < WORDS:
                continue
            stretch = []
            for j in range(max(0, i - BEFORE), min(len(segments), i + AFTER + 1)):
                mark = ">> " if j == i else "   "
                stretch.append(mark + segments[j]["text"].strip())
            item = {"vid": vid, "title": episodes[vid]["title"], "i": i,
                    "t": segments[i]["t"], "who": who,
                    "line": segments[i]["text"].strip(),
                    "stretch": "\n".join(stretch)}
            (his if who == ELON else theirs).append(item)
    rng = random.Random(seed)
    return rng.sample(his, min(each, len(his))) + rng.sample(theirs, min(each, len(theirs)))


async def judge(client, model: str, items: list[dict]) -> list[str]:
    body = "\n\n".join(f"Stretch {n}:\n{item['stretch']}"
                       for n, item in enumerate(items, 1))
    reply = await client.messages.create(
        model=model, max_tokens=200, system=SYSTEM,
        messages=[{"role": "user", "content": body}])
    text = "".join(block.text for block in reply.content
                   if getattr(block, "text", None))
    try:
        letters = json.loads(text[text.index("["):text.rindex("]") + 1])
    except ValueError:
        return ["U"] * len(items)
    letters = [str(x).strip().upper()[:1] for x in letters]
    return (letters + ["U"] * len(items))[:len(items)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--each", type=int, default=120,
                    help="lines to check on each side")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    settings = get_settings()
    client = AsyncAnthropic(**anthropic_client_kwargs(settings))
    items = sample(args.each, args.seed)
    batches = [items[at:at + PER_CALL] for at in range(0, len(items), PER_CALL)]
    # Two at a time: the proxy is shared with the live site and the bot.
    gate = asyncio.Semaphore(2)

    async def one(batch: list[dict]) -> None:
        async with gate:
            try:
                letters = await judge(client, settings.search_model, batch)
            except Exception as exc:                            # noqa: BLE001
                print(f"  a call failed: {redact(str(exc))[:120]}")
                letters = ["U"] * len(batch)
        for item, letter in zip(batch, letters, strict=True):
            item["read"] = letter

    await asyncio.gather(*(one(b) for b in batches))

    for side, want in (("named Elon Musk", "G"), ("named somebody else", "O")):
        mine = [i for i in items if (i["who"] == ELON) == (want == "G")]
        agree = sum(i["read"] == want for i in mine)
        unsure = sum(i["read"] == "U" for i in mine)
        against = [i for i in mine if i["read"] not in (want, "U")]
        print(f"\n  {side}: {len(mine)} lines · the words agree on {agree} · "
              f"cannot tell {unsure} · disagree {len(against)}")
        for item in against:
            stamp = f"{int(item['t']) // 60}:{int(item['t']) % 60:02}"
            print(f"\n     {item['title'][:48]} at {stamp} "
                  f"(named {item['who']})")
            for line in item["stretch"].splitlines():
                print(f"        {line[:150]}")
    if args.out:
        args.out.write_text(json.dumps(items, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

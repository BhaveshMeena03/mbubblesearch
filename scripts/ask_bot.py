"""What the X bot would reply to a post, without asking X anything.

    env -u ANTHROPIC_BASE_URL .venv/bin/python scripts/ask_bot.py \\
        "zec is at \\$907 right now" "@mbubbleSearch what price did ansem say he'd buy zcash again?"

Each argument is one paragraph of the post, as it would be typed on X.
A post with no @mbubbleSearch in it gets one added in front of the last
paragraph, which is where the question goes.

Builds the bot the way app/main.py does, with the X_BOT_ settings
production runs on (read from render.yaml, so a local .env cannot make
this answer differently from the live account), and calls compose():
the step that writes a reply, before the one that posts it. The X client
is a stand-in that refuses every call, so a run can neither post nor
spend a read. It does ask the model and the search index, the same as
one real reply.

For the daily "ask the bot" post from @Lexx_eth: the question goes out
only after this has shown the reply it will get.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HANDLE = "@mbubbleSearch"


def production_settings() -> dict[str, str]:
    """The X_BOT_ values render.yaml gives the live service. Secrets are
    "sync: false" there and have no value, so none are read."""
    spec = yaml.safe_load((ROOT / "render.yaml").read_text())
    found = {}
    for service in spec.get("services") or []:
        for var in service.get("envVars") or []:
            key = str(var.get("key") or "")
            if key.startswith("X_BOT_") and "value" in var:
                found[key] = str(var["value"])
    return found


class NoX:
    """An X client with the bot's own id and nothing else.

    `own_threads` is the threads this account started, read off its
    timeline by the live client; a new post is in none of them. Anything
    else is refused as missing, which is also what lets the bot's own
    getattr(..., default) checks fall back the way they would for a client
    that has not read anything yet.
    """

    def __init__(self, bot_user_id: str):
        self.bot_user_id = bot_user_id
        self.own_threads: set[str] = set()

    def __getattr__(self, name: str):
        raise AttributeError(f"the dry run never calls X (compose asked for {name!r})")


def post_text(parts: list[str]) -> str:
    parts = [" ".join(p.split()) for p in parts if p.strip()]
    if not parts:
        raise SystemExit("give the post, one argument per paragraph")
    if not any(HANDLE.lower() in p.lower() for p in parts):
        parts[-1] = f"{HANDLE} {parts[-1]}"
    return "\n\n".join(parts)


async def compose(text: str, author: str | None) -> str | None:
    os.environ.update(production_settings())
    from app import main as live
    from app.config import get_settings
    from app.podcast import PodcastIndex
    from app.summaries import SummaryStore
    from app.x_api import Mention
    from app.x_bot import MentionBot, load_guest_windows

    settings = get_settings()

    def archive(build, episodes):
        try:
            return build() if episodes() else None
        except Exception:                                       # noqa: BLE001
            return None

    rooms = {
        "podcast": PodcastIndex(),
        "elon": archive(lambda: PodcastIndex(namespace=live.ELON_NAMESPACE), live._elon_episodes),
        "mcg": archive(lambda: PodcastIndex(namespace=settings.mcg_namespace,
                                            index_name=settings.mcg_pinecone_index),
                       live._mcg_episodes),
        "tradfi": archive(lambda: PodcastIndex(namespace=settings.tradfi_namespace),
                          live._tradfi_episodes),
        "threadguy": archive(lambda: PodcastIndex(namespace=settings.threadguy_namespace),
                             live._threadguy_episodes),
        "rasmr": archive(lambda: PodcastIndex(namespace="rasmr"), live._rasmr_episodes),
    }
    shelves = {"podcast": live._podcast_shelf, "elon": live._shelf_of(live._elon_episodes),
               "mcg": live._shelf_of(live._mcg_episodes),
               "tradfi": live._shelf_of(live._tradfi_episodes),
               "threadguy": live._shelf_of(live._threadguy_episodes),
               "rasmr": live._shelf_of(live._rasmr_episodes)}
    for name, index in rooms.items():
        follow = getattr(index, "follow", None)
        if follow is not None:
            follow(shelves[name])

    bot = MentionBot(
        NoX(settings.x_bot_user_id), rooms["podcast"],
        elon_index=rooms["elon"], tradfi_index=rooms["tradfi"], mcg_index=rooms["mcg"],
        threadguy_index=rooms["threadguy"], rasmr_index=rooms["rasmr"],
        daily_reply_cap=settings.x_bot_daily_reply_cap,
        per_thread_cap=settings.x_bot_per_thread_cap,
        include_links=settings.x_bot_include_links,
        contract_address=settings.x_bot_contract_address,
        token_label=settings.x_bot_token_label,
        daily_spend_cap_usd=settings.x_bot_daily_spend_cap_usd,
        verified_only=settings.x_bot_verified_only,
        per_author_cap=settings.x_bot_per_author_cap,
        post_limit=settings.x_bot_post_limit,
        search_model=settings.x_bot_search_model or None,
        summary_limit=settings.x_bot_summary_limit,
        summaries=SummaryStore(),
        guest_windows=load_guest_windows(),
        groq_api_key=settings.groq_api_key,
        # Not recorded: this question was never asked on X.
        questions=None,
        priority_authors=settings.priority_author_ids,
        speaker_ids=settings.speaker_by_author_id,
        site=settings.x_bot_site,
        dev_author=settings.x_bot_dev_id,
    )
    ident = "1" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    mention = Mention(id=ident, text=text, author_id=author or settings.x_bot_dev_id,
                      conversation_id=ident, author_verified=True,
                      author_verified_type="blue",
                      created_at=datetime.now(UTC).isoformat(timespec="seconds"))
    return await bot.compose(mention)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paragraphs", nargs="+", help="the post, one argument per paragraph")
    ap.add_argument("--author", help="X user id asking (default: Lex, the dev account)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from app.config import redact
    from app.x_bot import weighted_length

    text = post_text(args.paragraphs)
    try:
        reply = await compose(text, args.author)
    except Exception as exc:                                    # noqa: BLE001
        raise SystemExit(f"could not compose: {redact(str(exc))[:300]}") from exc
    if args.json:
        print(json.dumps({"post": text, "reply": reply,
                          "length": weighted_length(reply) if reply else 0},
                         ensure_ascii=False, indent=1))
        return 0
    print(f"POST\n{text}\n")
    if reply is None:
        print("REPLY\n(none: the bot would stay quiet on this post)")
        return 1
    print(f"REPLY ({weighted_length(reply)} as X counts it)\n{reply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

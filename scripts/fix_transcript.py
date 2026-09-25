"""Correct a word the captions got wrong, everywhere it appears.

Auto-generated captions mangle names the show says constantly. STONK came
through as "stock" 25 times, Dregg as "drag", Jito as "GTO". Each one
means the ticker returns nothing from the episode that spends twenty
minutes on it, which is the one thing this archive is for.

Doing it by hand is four steps in an order that is easy to get wrong, and
getting it wrong is silent:

  1. edit the shelf
  2. re-embed, because the passage text lives in the vectors
  3. re-apply the speaker labels, because re-embedding regenerates the
     passage text and drops the name prefixes
  4. repack the gzip, because the server reads that and not the raw file

Skip 3 and every speaker label on that episode is gone. Skip 4 and the
live site still shows the old word while everything local looks fixed.

    python scripts/fix_transcript.py --episode <id> --from GTO --to Jito
    python scripts/fix_transcript.py --episode <id> --from GTO --to Jito --write

Dry by default: it prints every line it would touch and changes nothing.
Read them before passing --write. A wrong correction is worse than an
uncorrected one, because the archive then asserts something nobody said.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import episode_store  # noqa: E402
from app.podcast import PodcastIndex  # noqa: E402
from app.schemas import Episode  # noqa: E402

SHELVES = {
    "podcast": (ROOT / "data" / "episodes.json", None),
    "elon": (ROOT / "data" / "elon_episodes.json", "elon"),
    "tradfi": (ROOT / "data" / "tradfi_episodes.json", None),
}


def matching(word: str) -> re.Pattern:
    """Whole word, any case, with an optional possessive.

    "gto's airdrop" has to match on GTO, and "stock" must never match
    inside "stocks" when only the singular was meant.
    """
    return re.compile(rf"\b{re.escape(word)}(?='s\b|\b)", re.I)


def replace(text: str, pattern: re.Pattern, to: str) -> str:
    def one(m: re.Match) -> str:
        return to.capitalize() if m.group(0)[:1].isupper() else to
    return pattern.sub(one, text)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", required=True)
    ap.add_argument("--from", dest="wrong", required=True)
    ap.add_argument("--to", dest="right", required=True)
    ap.add_argument("--shelf", default="podcast", choices=sorted(SHELVES))
    ap.add_argument("--write", action="store_true",
                    help="apply it. Without this nothing changes.")
    args = ap.parse_args()

    path, namespace = SHELVES[args.shelf]
    rows = episode_store.load(path)
    row = next((r for r in rows if r["episode_id"] == args.episode), None)
    if row is None:
        raise SystemExit(f"{args.episode} is not on {path.name}")

    pattern = matching(args.wrong)
    hits = [(i, s) for i, s in enumerate(row["segments"])
            if pattern.search(s.get("text") or "")]
    print(f"  {row['title'][:60]}")
    print(f"  {len(hits)} line(s) contain {args.wrong!r}\n")
    for _, seg in hits:
        t = int(seg["t"])
        after = replace(seg["text"], pattern, args.right)
        print(f"  [{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}]")
        print(f"     - {seg['text'].strip()[:98]}")
        print(f"     + {after.strip()[:98]}")
    if not hits:
        return 0
    if not args.write:
        print("\n  dry run. Read the lines above, then pass --write.")
        return 0

    for i, seg in hits:
        row["segments"][i]["text"] = replace(seg["text"], pattern, args.right)
    episode_store.merge([row], path=path)
    print(f"\n  shelf updated ({len(hits)} lines)")

    index = (PodcastIndex(namespace=namespace) if namespace
             else PodcastIndex())
    episode = Episode(**{k: row[k] for k in
                         ("episode_id", "title", "url", "platform",
                          "published_at", "segments") if k in row})
    print(f"  re-embedded {await index.ingest([episode])} passages")

    # Only the broadcast carries speaker labels, and re-embedding above
    # has just dropped them from the passage text.
    if args.shelf == "podcast":
        subprocess.run([sys.executable, "scripts/apply_speaker_labels.py",
                        "--only", args.episode], cwd=ROOT, check=False)

    subprocess.run([sys.executable, "scripts/pack_episodes.py", str(path)],
                   cwd=ROOT, check=False)
    print(f"\n  done. Commit {path.with_suffix('.json.gz').name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

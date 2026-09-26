"""A live smoke test: the whole system, against the real indexes.

pytest covers units and fixtures. This covers the things that only break
in production and break silently: an archive answering from the wrong
corpus, a transcript correction that reached the shelf but not the gzip
the server actually reads, speaker labels wiped by a re-embed, one
person's lines reported as another's.

Every check here is a bug that shipped at least once.

    python scripts/verify_live.py

Costs a handful of embedding and model calls. Run it after anything that
touches transcripts, labels, routing or the prompts.
"""
import asyncio
import collections
import gzip
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.INFO)
ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = [], []
def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))

async def main():
    from app.config import get_settings
    from app.podcast import PodcastIndex
    from app.x_bot import as_speaker, corpus_for
    s = get_settings()

    print("\n== routing: four archives stay apart ==")
    for q, want in [
        ("what did ansem say about hyperliquid", "podcast"),
        ("what did elon tell lex fridman about mars", "elon"),
        ("what did larry fink say about bitcoin", "tradfi"),
        ("what did saylor say on lex fridman", "tradfi"),
        ("what did elon say at davos", "elon"),
        ("what did ansem say about blackrock", "podcast"),
        ("what did they say about the etf", "podcast"),
        ("did banks mention saylor on the show", "podcast"),
    ]:
        got = corpus_for(q)
        check(f"route {q[:42]!r}", got == want, f"{got} (want {want})")

    print("\n== the bot resolves 'I' for a host ==")
    check("ansem's 'i' becomes his name",
          as_speaker("what did i say about IMD", "Ansem") == "what did Ansem say about IMD")
    check("a stranger's 'i' is untouched",
          as_speaker("what did i say about IMD", None) == "what did i say about IMD")

    print("\n== transcript corrections landed everywhere ==")
    rows = json.loads((ROOT / "data" / "episodes.json").read_text())
    uniq: dict = {}
    for r in rows:
        uniq.setdefault(r["episode_id"], r)
    gto = sum(1 for v in uniq.values() for x in v.get("segments",[])
              if re.search(r"\bGTO\b", x["text"]))
    check("no GTO left on the broadcast shelf", gto == 0, f"{gto} found")
    ep = uniq["x-2103221357530263924"]
    stonk = sum(1 for x in ep["segments"] if re.search(r"\bstonk", x["text"], re.I))
    check("stonk correction present", stonk >= 14, f"{stonk} lines")
    packed = json.load(gzip.open(ROOT/"data"/"episodes.json.gz","rt",encoding="utf-8"))
    pep = next(r for r in packed if r["episode_id"]=="x-2103221357530263924")
    pg = sum(1 for x in pep["segments"] if re.search(r"\bstonk", x["text"], re.I))
    check("the shipped gzip has it too", pg == stonk, f"{pg} vs {stonk}")

    print("\n== speaker labels survived every re-embed ==")
    smap = json.loads((ROOT/"data"/"speaker_map.json").read_text())
    c = collections.Counter(smap["x-2103221357530263924"].values())
    check("rasmr labelled", c.get("rasmr",0) == 201, f"{c.get('rasmr',0)}")
    check("ansem labelled", c.get("Ansem",0) == 627, f"{c.get('Ansem',0)}")
    check("banks not in this episode", "FaZe Banks" not in c)

    print("\n== live retrieval, all four archives ==")
    idx = PodcastIndex()
    r = await idx.search("what did rasmr say about stonk pairs")
    check("broadcast answers on the corrected ticker",
          "stonk" in r.answer.lower(), r.answer[:60])
    check("no em dash in the answer", "—" not in r.answer)
    r2 = await PodcastIndex(namespace=s.tradfi_namespace).search(
        "what did jamie dimon say about bitcoin")
    check("finance archive answers on dimon", len(r2.answer) > 80, r2.answer[:60])
    check("no em dash in the finance answer", "—" not in r2.answer)

    print("\n== attribution: no cross-speaker leakage ==")
    for _ in range(3):
        a = (await idx.search("what does rasmr think about realized pnl")).answer
        leak = any(x in a for x in ("keep the capital","keep your profits","1:37:52"))
        check("rasmr answer carries none of ansem's lines", not leak)

    print(f"\n{'='*58}\n  {len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"    FAILED: {f}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))

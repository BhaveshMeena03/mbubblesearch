"""make_clip's caption fallback reads the archive a video is actually in.

It only ever read MCG's index, so a ThreadGuy interview with no YouTube
auto-captions (Koolkrypto's, 2026-10-01) could not be clipped at all.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import make_clip  # noqa: E402


class S:
    pinecone_index = "default-index"
    threadguy_namespace = "threadguy"
    mcg_pinecone_index = "mcg-index"
    mcg_namespace = "mcg"


def test_a_threadguy_video_reads_threadguys_namespace():
    vid = json.loads((ROOT / "data" / "threadguy_index.json").read_text())[0]["id"]
    assert make_clip.where_is(vid, S()) == ("default-index", "threadguy")


def test_anything_else_still_reads_mcg():
    assert make_clip.where_is("not-a-threadguy-id", S()) == ("mcg-index", "mcg")

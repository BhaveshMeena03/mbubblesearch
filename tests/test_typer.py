"""Every page types its answer out the way the Market Bubble page does.

demo/typer.js is run under node, the same file the browser loads. The
pages are read as text: each loads the typer before its own script and
draws through it, and none is left redrawing on its old timer, which put
an answer on screen a burst at a time.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TYPER = ROOT / "demo" / "typer.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

PAGES = ["podcast.html", "mcg.html", "elon.html", "threadguy.html", "rasmr.html",
         "home.html", "finance.html", "posts.html"]


def run(script: str) -> dict:
    """Runs `script` with T bound to the typer, no browser, so every tick
    comes from the timer that stands in for missing animation frames."""
    out = subprocess.run([NODE, "-e", "const T=require(process.argv[1]);" + script,
                          str(TYPER)], capture_output=True, text=True, check=True,
                         timeout=30)
    return json.loads(out.stdout)


@needs_node
def test_a_burst_is_typed_out_over_several_ticks_and_finishes_whole():
    got = run("""
      const drawn=[]; const t=T(s=>drawn.push(s));
      t.push('a'.repeat(120)); t.push('b'.repeat(200));
      t.finish(all=>console.log(JSON.stringify({all, drawn: drawn.map(d=>d.length)})));
    """)
    assert got["all"] == "a" * 120 + "b" * 200
    lengths = got["drawn"]
    assert len(lengths) > 1, "a burst landed in one jump"
    assert lengths == sorted(lengths) and lengths[-1] == 320


@needs_node
def test_finish_waits_for_what_is_still_queued():
    got = run("""
      const t=T(()=>{}); let early=null;
      t.push('x'.repeat(400));
      t.finish(all=>{ console.log(JSON.stringify({len: all.length, early})); });
      early = t.text().length;
    """)
    assert got["len"] == 400 and got["early"] == 400


@needs_node
def test_a_stopped_typer_never_draws_again_or_calls_back():
    got = run("""
      let drawn=0, called=false; const t=T(()=>{drawn++;});
      t.push('y'.repeat(300)); t.finish(()=>{called=true;}); t.stop();
      setTimeout(()=>console.log(JSON.stringify({drawn, called})), 400);
    """)
    assert got == {"drawn": 0, "called": False}


@needs_node
def test_finish_with_nothing_queued_calls_back_at_once():
    got = run("""
      const t=T(()=>{}); let now=false; t.finish(()=>{now=true;});
      console.log(JSON.stringify({now}));
    """)
    assert got == {"now": True}


@needs_node
def test_the_pace_is_the_market_bubble_pages():
    """2 to 14 characters a frame, a twelfth of the queue, and a late tick
    owes the frames it missed, so a page with no animation frames types at
    the same speed on its timer."""
    got = run("console.log(JSON.stringify({s:[1,24,120,500].map(T.step),"
              "o:[16,120,1000].map(T.owed)}))")
    assert got == {"s": [2, 2, 10, 14], "o": [1, 8, 60]}


def test_frames_have_a_timer_behind_them():
    """A preview pane and an occluded window both report "visible" and get
    no animation frames; the answer stalled there before this."""
    js = TYPER.read_text()
    assert "requestAnimationFrame(run)" in js and "setTimeout(run," in js
    assert "document.hidden" in js and "prefers-reduced-motion" in js
    assert "—" not in js and "–" not in js


@pytest.mark.parametrize("page", PAGES)
def test_every_page_loads_the_typer_before_its_own_script(page):
    html = (ROOT / "demo" / page).read_text()
    tag = '<script src="/demo/typer.js"></script>'
    assert html.count(tag) == 1, page
    # Not deferred: a shared ?q= link asks before the page has finished.
    first_inline = re.search(r"<script>\s", html).start()
    assert html.index(tag) < first_inline, page
    assert "Typer(" in html, page


@pytest.mark.parametrize("page", ["home.html", "threadguy.html", "rasmr.html"])
def test_no_page_redraws_a_burst_at_a_time_any_more(page):
    html = (ROOT / "demo" / page).read_text()
    assert "setTimeout(draw, 40)" not in html
    assert "typer.finish(resolve)" in html


@pytest.mark.parametrize("page", ["mcg.html", "elon.html"])
def test_times_are_linked_once_the_last_word_is_on_screen(page):
    html = (ROOT / "demo" / page).read_text()
    assert "acc += p.text; answerText(acc);" not in html
    finish = html.index("typer.finish(function(){")
    assert html.index("citeTimestamps(hits);", finish) - finish < 200

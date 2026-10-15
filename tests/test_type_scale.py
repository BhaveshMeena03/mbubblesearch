"""One reading scale on every page that answers a question.

Measured on 15 Oct 2026, the pages disagreed: Market Bubble had the
biggest search box (19px) and the smallest answer (15px) and passages
(13px), the Musk and MCG Ask buttons were 11.5 to 12px, and no two pages
set their example questions alike. Each page keeps its own fonts and its
own headline; what a reader reads is now one size everywhere:

    answer 17, search box 17, passages 14.5, example questions 14.5,
    Ask button 14.

The posts page is the one exception, on purpose: its answer is his own
words set in the display serif at 21px, and 17px of that face reads
smaller than 17px of Inter. Phones keep their own rules (16px boxes,
so iOS does not zoom), which is why these are checked as written rather
than appended as overrides.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SCALE = {
    "podcast.html": ["padding-left:24px;font-size:17px;",          # box
                     "border-radius:12px;font-size:14px;line-height:1;",  # Ask
                     "padding:7px 14px;font-size:14.5px;",          # examples
                     "#answer-body{font-size:17px;line-height:1.65}",
                     ".quote{font-size:14.5px;"],                   # passages
    "home.html": ["padding:19px 22px;font:inherit;font-size:17px;",
                  "font-family:var(--mono);font-weight:600;font-size:14px;",
                  "font-family:inherit;font-size:14.5px;",
                  ".answer{--caret:var(--signal);font-size:17px;",
                  ".hit p{margin:9px 0 0;color:var(--ink-2);font-size:14.5px;"],
    "threadguy.html": ["padding:18px 20px;font:inherit;font-size:17px;",
                       "font-weight:600;font-size:14px;letter-spacing:.18em;",
                       "font:inherit;font-size:14.5px;cursor:pointer;",
                       ".answer{--caret:var(--sky);font-size:17px;",
                       ".hit p{margin:9px 0 0;color:var(--ink-2);font-size:14.5px;"],
    "rasmr.html": ["padding:18px 20px;font:inherit;font-size:17px;",
                   "font-weight:600;font-size:14px;letter-spacing:.18em;",
                   "font:inherit;font-size:14.5px;cursor:pointer;",
                   ".answer{--caret:var(--sky);font-size:17px;",
                   ".hit p{margin:9px 0 0;color:var(--ink-2);font-size:14.5px;"],
    "mcg.html": ['monospace;font-size:17px;padding:19px 13px}',
                 'monospace;font-size:14px;font-weight:600;',
                 'monospace;font-size:14.5px;',
                 ".answer .body{padding:20px 18px;font-size:17px;",
                 ".said{margin:8px 0 0;font-size:14.5px}"],
    "elon.html": ['monospace;font-size:17px;padding:20px 14px}',
                  'monospace;font-size:14px;font-weight:600;',
                  'monospace;font-size:14.5px;padding:7px 12px;',
                  ".answer .body{padding:22px 20px;font-size:17px;",
                  ".said{margin:9px 0 0;color:var(--ink);font-size:14.5px}"],
    "finance.html": ["font: inherit; font-size: 17px; color: var(--ink);",
                     "font: inherit; font-size: 14px; font-weight: 600;",
                     "font: inherit; font-size: 14.5px; cursor: pointer;",
                     ".answer .said { --caret: var(--accent); font-size: 17px;",
                     ".cite p { margin: 0; color: var(--ink-2); font-size: 14.5px;"],
    "posts.html": ["font:inherit;\n    font-size:17px;outline:none}",
                   "#go{font:inherit;font-weight:650;font-size:14px;",
                   ".chip{font:inherit;font-size:14.5px;",
                   ".words{font-size:14.5px;"],
}


@pytest.mark.parametrize("page, rules", SCALE.items())
def test_every_page_reads_at_one_scale(page, rules):
    css = (ROOT / "demo" / page).read_text().split("</style>")[0]
    for rule in rules:
        assert rule in css, f"{page}: {rule}"


@pytest.mark.parametrize("page", ["podcast.html", "mcg.html", "elon.html", "posts.html"])
def test_phones_keep_a_box_big_enough_that_ios_does_not_zoom(page):
    assert "#q{font-size:16px" in (ROOT / "demo" / page).read_text()

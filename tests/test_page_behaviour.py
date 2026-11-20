"""Two promises the page makes, guarded at the source.

Neither is checkable from Python at runtime — they live in the browser —
so this reads the page and asserts the shapes are still there. It is a
cheap guard against the two regressions that would be invisible in the
test suite and obvious to a visitor.

A timestamp inside an episode summary used to be a link that opened
YouTube in a new tab, which is exactly what the rest of the page stopped
doing when citations learned to play in place. A moment the YouTube
upload cut keeps a link, to the live broadcast on X at that second, and
a time that can be opened nowhere is printed as text: a control that
opened the show at 0:00 would promise more than it does.

And the note explaining that X cannot jump used to sit under the results
heading, so a set where four of six results played announced to all six
that nothing would. It belongs on the cards it is true of.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAGE = (ROOT / "demo" / "podcast.html").read_text()


def test_every_summary_can_be_shared_by_link():
    """A summary already opened from #ep-<id>, but only for someone who
    knew to build that address. Each card now carries a button that copies
    it, and the address it copies is the one the page opens from."""
    assert 'share.className="share ep-share"' in PAGE
    assert 'el.id="ep-"+ep.episode_id;' in PAGE
    assert 'location.origin+location.pathname+"#ep-"+ep.episode_id' in PAGE
    assert 'want.indexOf("ep-") === 0' in PAGE
    # Followed from the page itself, only the hash changes and nothing
    # reloads; the card has to open on that too.
    assert 'addEventListener("hashchange", goToHash)' in PAGE


def test_a_summary_timestamp_plays_rather_than_navigating_away():
    assert 'slot.className="player ep-player"' in PAGE
    assert 'cell.type="button"; cell.className="at"; cell.textContent=label;' in PAGE
    assert "playIn(box,yt[0],yt[1],true," in PAGE


def test_a_moment_the_upload_cut_keeps_its_outbound_link():
    """Three shapes, in this order: plays here, opens X at the second,
    or is text. The link is marked with an arrow so it does not look
    like the button beside it."""
    plays = PAGE.index("if(yt && VIDEO.test(yt[0]||\"\")){")
    leaves = PAGE.index('cell.className="at out"; cell.href=b.link;')
    text = PAGE.index('cell.className="at off"; cell.textContent=label;')
    assert plays < leaves < text
    assert '.notes a.at::after{content:" \\2197"' in PAGE


def test_the_scrub_note_is_not_attached_to_the_heading():
    """It reads as a property of the result set when it is a property of
    one result."""
    assert "'<h3>'+head+'</h3>';" in PAGE
    assert "'<h3>'+head+'</h3>'+note" not in PAGE


def test_every_card_offers_a_way_into_the_moment():
    """There is no such thing as an unjumpable card any more.

    This asserted the opposite: that a broadcast showed a note telling the
    reader to scrub by hand, because X was believed to have no timestamp
    parameter. It has one — ?t=<seconds> on the status URL opens the player
    at that second — so a broadcast now gets a real control like anything
    else. What differs is only where it opens.
    """
    actions = PAGE[PAGE.index('class="hit-actions"'):PAGE.index('class="clipbox"')]
    assert "play here" in actions, "no inline play for embeddable hits"
    assert "play on X" in actions, "a broadcast must still offer its moment"
    assert "seeknote" not in actions, "the scrub-by-hand note is obsolete"


def test_only_one_player_can_be_open():
    """Two videos playing at once is the worst failure this page has, and
    the reset runs across every slot, not only the ones nearby."""
    quiet = PAGE[PAGE.index("function quiet(){"):][:400]
    assert 'document.querySelectorAll(".player").forEach' in quiet
    # An episode, a time in its summary, a card, a time in the answer,
    # the example and a new question all go through it.
    assert PAGE.count("quiet();") >= 5

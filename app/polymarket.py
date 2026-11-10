"""Polymarket on Market Bubble: the markets the show put on screen.

Every Market Bubble broadcast runs a Polymarket ticker along the bottom of
the frame, a scrolling line of questions with the odds beside each one:
"Will Bitcoin dip to $80,000 in October? YES 77%". Hardly any of it is ever
said out loud. This keeps a record of what was shown, when, at what odds,
and how each market came out.

A row reaches the page only after four checks, and every one of them can
only take a row away.

  the words   The ticker is read off the video by OCR. A reading is only
              ever used to find a market; what the page shows is the
              market's own question from Polymarket. To count as the same
              market, the two must agree on every number and every month
              in them, and be near enough letter for letter. Polymarket
              runs sister markets that differ by one figure ("dip to
              $80,000", "dip to $82,500"), and those must never be mixed up.
  the time    When a frame was on air comes from X's own playlist, which
              stamps every two-second segment with the moment it went out.
              The clock in the corner of the frame is read as well, and
              the two have to agree, or the episode is not used at all.
  the price   Polymarket keeps a minute by minute price history. When a
              market was on screen, it must have traded at the odds the
              ticker showed, within two points, at that minute or in the
              few minutes before (the ticker refreshes every few minutes,
              see TICKER_LAG). That one test proves the market is the
              right one and the time is right.
              It is also what tells apart markets that share a question
              word for word, which Polymarket does: there were six markets
              called "Will Bitcoin dip to $80,000 in October?" in October
              2026 alone, opened on different days.
  a person    Nothing ships unless data/polymarket_verdicts.json says keep.
              The default is no.

The odds are the market's. They are never presented as anything a host
said or predicted: the ticker is a sponsor's, and Ansem and Banks did not
choose what ran on it.

This module is pure: no network, no subprocess. The ticker is read by
scripts/polymarket_ticker.py on the laptop, where X's video is reachable,
and matched by scripts/polymarket_match.py on a GitHub runner, where
Polymarket is (it is blocked from India, where the laptop is).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

# Where the ticker and the clock sit on a 1920x1080 broadcast frame, as
# (x, y, width, height). Measured on the 8 Oct, 22 Oct, 29 Oct and 5 Nov
# 2026 shows, which all put them in the same place.
TICKER_BOX = (355, 932, 1180, 50)
CLOCK_BOX = (1745, 765, 170, 45)
FRAME_SIZE = (1920, 1080)

# The ticker rounds Polymarket's price to a whole percent and is read off a
# moving picture, so it is allowed to be this far from the price history.
# On the two markets checked by hand it was 0.5 away (68 against 67.5, 77
# against 76.5).
ODDS_TOLERANCE = 2.0
# A price point this close to the moment of a sighting is the price then.
PRICE_WINDOW = 180
# The share of a market's sightings whose odds must agree with its history.
MIN_AGREEMENT = 0.8
# How alike, letter for letter, a reading and a question have to be once
# both are reduced to letters and digits. Numbers and months are held to
# exact agreement separately; this only has to catch a different sentence.
MIN_RATIO = 0.92
# A reading cut off at the left edge is still usable when this much of the
# question is left in it.
MIN_TAIL = 25

EASTERN = ZoneInfo("America/New_York")
MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")

# "...October? YES 77%". The question mark is what ends a question; OCR
# sometimes puts a stray quote between it and the odds.
_ODDS = re.compile(r"\?\s*[\"'`\u2018\u2019]?\s*YES\s*(\d{1,3})\s*%", re.I)
# Between one market and the next the ticker draws the market's round icon,
# which OCR turns into a short run of junk: "(—)", "@®", "Bb", "re", "2)".
_STARTERS = {"will", "willthe", "vill", "next", "who", "which", "what", "how",
             "is", "does", "did", "can", "are", "no"}


@dataclass(frozen=True)
class Reading:
    """One market as read off one frame."""
    text: str          # the question as OCR read it, ending in "?"
    yes: int           # the odds beside it, in percent
    whole: bool        # False when the frame's left edge cut its start off


def readings(line: str) -> list[Reading]:
    """Every market a line of ticker text shows in full or in part.

    Only a question followed by its odds is taken. Whatever trails after
    the last odds on the line is the start of a market still coming in
    from the right, and the next frames will show it whole.
    """
    out: list[Reading] = []
    start, first = 0, True
    for m in _ODDS.finditer(line):
        body = line[start:m.start()]
        start = m.end()
        if first:
            # The first market on a line may have run off the left edge.
            # It is whole only when its opening word is there to see,
            # straight after the icon or the edge.
            text, starts = _strip_icon(body, reach=3)
            whole = starts
            if not whole:
                text = body.strip()
        else:
            text, _ = _strip_icon(body, reach=4)
            whole = True
        first = False
        yes = int(m.group(1))
        if not 0 <= yes <= 100 or len(squash(text)) < 12:
            continue
        out.append(Reading(text=re.sub(r"\s+", " ", text).strip() + "?",
                           yes=yes, whole=whole))
    return out


def _strip_icon(body: str, *, reach: int) -> tuple[str, bool]:
    """The question without the icon junk in front of it, and whether it
    was seen to start with a question word.

    "will" is taken in either case, because OCR has read the W small;
    every other question word only with its capital, so a "can" or an "is"
    in the middle of a cut-off sentence is not taken for a start.
    """
    tokens = body.split()
    for i, tok in enumerate(tokens[:reach]):
        word = squash(tok)
        if word in _STARTERS and (word.startswith(("will", "vill")) or tok[:1].isupper()):
            # The icon comes out as a few short scraps; anything longer in
            # front of the question word is words, and the start is not here.
            if all(len(t) <= 3 or _junk(t) for t in tokens[:i]):
                return " ".join(tokens[i:]), True
    while tokens and _junk(tokens[0]):
        tokens.pop(0)
    return " ".join(tokens), False


def _junk(tok: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", tok)
    if not letters:
        return not re.search(r"\d", tok) or bool(re.search(r"[()<>|{}\[\]]", tok))
    if re.search(r"[()<>|{}\[\]®©€¢“”‘’»«]", tok):
        return True
    # Short and lowercase ("re", "ee", "cy") or mixed case ("Bb", "Seg")
    # is the icon. Short and all capitals ("US", "UK", "BTC") is a word.
    return len(letters) <= 3 and not letters.isupper()


def squash(text: str) -> str:
    """Letters and digits only, lowercased: what OCR spacing cannot move."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


# OCR gets a letter or two wrong; a different question differs by a word.
# "Will Apple be the largest company..." and "Will NVIDIA be the largest
# company..." are 93% alike letter for letter, with the same date: only the
# size of the one stretch where they differ tells them apart.
MAX_DIFF_RUN = 3


def alike(a: str, b: str) -> bool:
    """Whether two squashed strings are the same text up to OCR slips:
    nearly the same letter for letter, and nowhere differing by more than
    MAX_DIFF_RUN letters in a row."""
    match = SequenceMatcher(None, a, b, autojunk=False)
    if match.ratio() < MIN_RATIO:
        return False
    return all(max(i2 - i1, j2 - j1) <= MAX_DIFF_RUN
               for op, i1, i2, j1, j2 in match.get_opcodes() if op != "equal")


def numbers(text: str) -> list[str]:
    """Every figure in a question, as a comparable string.

    "$80,000" and "80000" are the same figure; "December 31,2028" is two
    figures, 31 and 2028, because a comma before four digits is a missing
    space and not a thousands separator. "1.20" and "1.2" are the same.
    """
    t = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", text)
    t = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", t)
    out = []
    for n in re.findall(r"\d+(?:\.\d+)?", t):
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.append(n.lstrip("0") or "0")
    return sorted(out)


def months(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return sorted(w for w in words if w in MONTHS)


def same_question(reading: str, question: str, *, whole: bool) -> tuple[bool, str]:
    """Whether a reading is this question, and if not, why not.

    Both are compared from the end, where a reading is never cut: a whole
    reading may carry icon junk in front, a partial one is missing its
    start. Figures and months must agree exactly. For a partial reading,
    the figures it does show must all be the question's, but the first
    word is dropped first, because the edge of the frame can cut a figure
    in half.
    """
    r, q = squash(reading), squash(question)
    if not r or not q:
        return False, "empty"
    if whole:
        if len(r) < len(q) - 2:
            return False, "shorter than the question"
        tail = r[-len(q):]
        ratio = SequenceMatcher(None, tail, q, autojunk=False).ratio()
        if numbers(reading) != numbers(question):
            # Icon junk can carry a digit ("2)", "5)"). Forgive a figure the
            # question lacks only when it sits outside the aligned part.
            if numbers(_tail_text(reading, len(q))) != numbers(question):
                return False, "figures differ"
        if months(reading) != months(question):
            if months(_tail_text(reading, len(q))) != months(question):
                return False, "months differ"
    else:
        if len(r) < MIN_TAIL:
            return False, "too little of it was visible"
        shown = reading.split(None, 1)[1] if " " in reading else reading
        tail_q = q[-len(r):]
        ratio = SequenceMatcher(None, r, tail_q, autojunk=False).ratio()
        tail = r
        have, want = numbers(shown), numbers(question)
        if any(have.count(n) > want.count(n) for n in have):
            return False, "figures differ"
        if any(mo not in months(question) for mo in months(shown)):
            return False, "months differ"
    if ratio < MIN_RATIO:
        return False, f"words differ ({ratio:.2f})"
    if not alike(tail, q if whole else tail_q):
        return False, "a word differs"
    return True, f"{ratio:.2f}"


def _tail_text(text: str, squashed_len: int) -> str:
    """The end of text covering squashed_len letters and digits."""
    count = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i].isalnum():
            count += 1
            if count >= squashed_len:
                return text[i:]
    return text


# --- the time ------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    """One two-second piece of the broadcast replay."""
    uri: str
    start: float       # seconds into the replay: what ?t= on X seeks to
    duration: float
    aired: datetime    # when it went out, from #EXT-X-PROGRAM-DATE-TIME


def playlist(text: str) -> list[Segment]:
    """The segments of an HLS replay, with when each one aired.

    A segment without a program date time is dated from the one before it
    plus its length. A replay whose first segment has none cannot be dated
    at all, and raises: guessing the start would shift every row.
    """
    out: list[Segment] = []
    at, duration, aired = 0.0, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            aired = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",")[0])
        elif line and not line.startswith("#"):
            if duration is None:
                raise ValueError(f"segment without a length: {line[:60]}")
            if aired is None:
                if not out:
                    raise ValueError("the replay does not say when it aired")
                prev = out[-1]
                aired = prev.aired + timedelta(seconds=prev.duration)
            out.append(Segment(uri=line, start=round(at, 3), duration=duration,
                               aired=aired.astimezone(UTC)))
            at += duration
            duration, aired = None, None
    return out


def sample(segments: list[Segment], every: int) -> list[Segment]:
    """One segment in every `every`, from the first."""
    if every < 1:
        raise ValueError("every must be at least 1")
    return segments[::every]


# The corner clock: "05:36 PM EST" under the date. Its font draws zero as a
# slashed circle, which OCR reads as "@", "A", "a", "2" or "4", so the first
# digit of the hour is unreliable and is not used. The minutes and the zone
# read cleanly, and they are enough: a replay dated wrongly is wrong by
# seconds or minutes, which the minutes show, and a whole hour can only
# come from the zone, which is read too.
_CLOCK = re.compile(r"(\S?)\s*[:.;]\s*(\d{2})\s*(AM|PM)\s*(EST|EDT)", re.I)


def read_clock(text: str) -> tuple[int, str] | None:
    """(minute, zone) from the frame's corner clock, or None.

    "@" and "O" next to a digit are read as zero, the one confusion that
    turned up in the minutes. Anything that is not a minute is dropped,
    never repaired.
    """
    t = re.sub(r"(?<=\d)[@Oo]|[@Oo](?=\d)", "0", text)
    m = _CLOCK.search(t)
    if not m:
        return None
    minute = int(m.group(2))
    if minute > 59:
        return None
    return minute, m.group(4).upper()


def clock_offset(aired: datetime, clock: tuple[int, str]) -> int | None:
    """How many minutes the corner clock is from when the frame went out,
    between -30 and 29, or None when its zone is the wrong one (EDT shown
    in November, say), which no offset explains."""
    minute, zone = clock
    local = aired.astimezone(EASTERN)
    if zone != ("EDT" if local.dst() else "EST"):
        return None
    return (minute - local.minute + 30) % 60 - 30


def clock_holds(offsets: list[int | None]) -> bool:
    """Whether a replay's dates agree with what its frames showed.

    The clock's font is hard on OCR, and it errs the same few ways: 0 read
    as 4 or 8, 5 as 3, 8 as 5 (5 Nov 2026: 17:52 read "32", 18:40 read
    "44"). A misread digit throws the offset somewhere random, and no two
    misreads land in the same place often. A replay dated wrongly is the
    other way round: every frame read right lands on the same wrong offset.

    So what is asked is where the readings pile up. At least two in five
    must be within a minute of the playlist (the clock shows whole minutes,
    so a right replay reads 0 or 1 behind), and those must outnumber any
    other single offset at least three to one, which puts the pile at zero.
    A clock in the wrong zone (EDT in November) on more than one frame in
    five fails outright.
    """
    read = [o for o in offsets if o is not None]
    if len(offsets) < 10 or len(offsets) - len(read) > len(offsets) // 5 or not read:
        return False
    counts = Counter(read)
    near = sum(counts[o] for o in (-1, 0, 1))
    far = max((n for o, n in counts.items() if abs(o) > 1), default=0)
    return near >= 0.4 * len(offsets) and near >= 3 * max(far, 1)


# --- grouping readings into markets ---------------------------------------------

@dataclass
class Sighting:
    """A market seen once: on which frame, at which odds."""
    t: float           # seconds into the replay
    aired: datetime
    yes: int
    text: str
    whole: bool


@dataclass
class Shown:
    """Every sighting of one market in one episode."""
    sightings: list[Sighting] = field(default_factory=list)

    @property
    def text(self) -> str:
        """What the ticker said, by majority over the whole readings."""
        whole = [s.text for s in self.sightings if s.whole] or [s.text for s in self.sightings]
        votes: dict[str, list[str]] = {}
        for t in whole:
            votes.setdefault(squash(t), []).append(t)
        best = max(votes.values(), key=lambda v: (len(v), len(v[0])))
        return best[0]

    @property
    def first(self) -> Sighting:
        return min(self.sightings, key=lambda s: s.t)

    @property
    def last(self) -> Sighting:
        return max(self.sightings, key=lambda s: s.t)


def _same_market(text: str, whole: bool, known: str) -> bool:
    """Whether a reading is the market a group already holds (`known`,
    always a whole reading).

    Two whole readings are compared end to end, with every figure and month
    equal. A cut-off reading is compared from the end, over what is left of
    it, and only when enough is left: a short question like "Netanyahu out
    by June 30, 2027?" has to be seen whole to be grouped at all.
    """
    st, sk = squash(text), squash(known)
    if whole:
        return (alike(st, sk) and numbers(text) == numbers(known)
                and months(text) == months(known))
    if len(st) < MIN_TAIL or len(st) > len(sk) + 2:
        return False
    if not alike(st, sk[-len(st):]):
        return False
    shown = text.split(None, 1)[1] if " " in text else text
    have, want = numbers(shown), numbers(known)
    return (all(have.count(x) <= want.count(x) for x in have)
            and all(mo in months(known) for mo in months(shown)))


def group(sightings: list[Sighting]) -> list[Shown]:
    """Sightings gathered into markets.

    Whole readings go first and found the groups. A partial reading joins
    the one group it fits, and is dropped when it fits none or more than
    one: "...in November?" alone could be any of a dozen markets.
    """
    groups: list[Shown] = []
    for s in sorted(sightings, key=lambda s: (not s.whole, -len(squash(s.text)), s.t)):
        fits = [g for g in groups if _same_market(s.text, s.whole, g.text)]
        if s.whole and not fits:
            groups.append(Shown([s]))
        elif len(fits) == 1:
            fits[0].sightings.append(s)
        elif s.whole and len(fits) > 1:
            # A whole reading that fits two groups means they were the same
            # market all along. Merge them.
            keep = fits[0]
            for g in fits[1:]:
                keep.sightings.extend(g.sightings)
                groups.remove(g)
            keep.sightings.append(s)
    for g in groups:
        g.sightings.sort(key=lambda s: s.t)
    return groups


# --- the price -----------------------------------------------------------------

def price_at(history: list[dict], when: datetime) -> float | None:
    """Polymarket's price for a market at a moment, from its history
    ([{"t": unix seconds, "p": 0..1}]), or None when no point is near."""
    target = when.timestamp()
    best = min(history, key=lambda p: abs(p["t"] - target), default=None)
    if best is None or abs(best["t"] - target) > PRICE_WINDOW:
        return None
    return float(best["p"])


# The ticker does not show the price of the moment. It refreshes every few
# minutes, so in a fast market what is on screen can be a price from a
# little earlier. Measured on 29 Oct 2026, the night of the Binance hack: of
# 80 sightings that disagreed with Polymarket at their own minute, 61
# matched a price from the ten minutes before and only 14 one from the ten
# minutes after. On the quiet 5 Nov show nearly every sighting agreed at
# the minute. So a sighting agrees when its odds are a price the market
# had at some point in the ten minutes up to the frame.
TICKER_LAG = 600


def _gaps(s: Sighting, history: list[dict]) -> tuple[float | None, float | None]:
    """How far a sighting's odds were, in points, from the market at its
    minute, and from the nearest price it had in the window the ticker
    could have been showing. None where there was no price at all."""
    at = s.aired.timestamp()
    now = price_at(history, s.aired)
    opens = at - TICKER_LAG
    window = [p["p"] for p in history if opens <= p["t"] <= at + PRICE_WINDOW]
    # A price holds until the next one, so the price in force when the
    # window opens counts too, however long before that it was set.
    before = [p for p in history if p["t"] < opens]
    if before:
        window.append(max(before, key=lambda p: p["t"])["p"])
    strict = None if now is None else abs(now * 100 - s.yes)
    loose = min((abs(p * 100 - s.yes) for p in window), default=None)
    return strict, loose


def agrees(s: Sighting, history: list[dict], *, at_minute: bool = False) -> bool:
    """Whether the market bears a sighting's odds out: at its own minute,
    or (by default) anywhere in the window the ticker could be showing."""
    strict, loose = _gaps(s, history)
    gap = strict if at_minute else loose
    return gap is not None and gap <= ODDS_TOLERANCE


@dataclass(frozen=True)
class Agreement:
    agree: int             # within the ticker's window
    compared: int
    worst: float | None    # the furthest any sighting was from every price in its window
    strict: int = 0        # of those, how many agreed at their own minute

    @property
    def share(self) -> float:
        return self.agree / self.compared if self.compared else 0.0

    @property
    def strict_share(self) -> float:
        return self.strict / self.compared if self.compared else 0.0

    @property
    def holds(self) -> bool:
        """Most sightings agree, and at least one agrees at its own minute.
        Agreement only through the ticker's lag is never enough alone: on
        29 Oct a single reading of "US x Iran ceasefire continues through
        November 4?" said 83% when the market was at 70, and nothing but
        the lag window joined the two."""
        return self.compared >= 1 and self.share >= MIN_AGREEMENT and self.strict >= 1


def agreement(sightings: list[Sighting], history: list[dict]) -> Agreement:
    agree = compared = strict = 0
    worst = None
    for s in sightings:
        at_minute, loose = _gaps(s, history)
        if loose is None:
            continue
        compared += 1
        agree += loose <= ODDS_TOLERANCE
        strict += at_minute is not None and at_minute <= ODDS_TOLERANCE
        worst = loose if worst is None else max(worst, loose)
    return Agreement(agree, compared, None if worst is None else round(worst, 2), strict)


def open_at(market: dict, when: datetime) -> bool:
    """Whether a market was trading at that moment. One that had already
    closed cannot have been the one on screen."""
    start = _when(market.get("startDate") or market.get("createdAt"))
    if start is None or start > when:
        return False
    closed_at = _when(market.get("closedTime"))
    return not (market.get("closed") and closed_at is not None and closed_at < when)


def _when(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if re.search(r"[+-]\d\d$", text):
        text += ":00"
    try:
        at = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def outcome(market: dict) -> dict:
    """How a market stands: resolved yes or no, closed and waiting, or
    open at its current price."""
    prices = _listish(market.get("outcomePrices"))
    names = [str(n).lower() for n in _listish(market.get("outcomes"))]
    yes_price = None
    if prices and "yes" in names and len(prices) == len(names):
        try:
            yes_price = float(prices[names.index("yes")])
        except (TypeError, ValueError):
            yes_price = None
    if market.get("closed"):
        if str(market.get("umaResolutionStatus") or "").lower() == "resolved" \
                and yes_price in (0.0, 1.0):
            return {"state": "resolved", "result": "yes" if yes_price == 1.0 else "no"}
        return {"state": "closed", "result": None}
    return {"state": "open", "result": None,
            "yes_now": None if yes_price is None else round(yes_price * 100, 1)}


def _listish(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.startswith("["):
        import json
        try:
            got = json.loads(value)
        except ValueError:
            return []
        return got if isinstance(got, list) else []
    return []


def yes_token(market: dict) -> str | None:
    tokens = _listish(market.get("clobTokenIds"))
    names = [str(n).lower() for n in _listish(market.get("outcomes"))]
    if len(tokens) == len(names) and "yes" in names:
        return str(tokens[names.index("yes")])
    return None


# --- the board -----------------------------------------------------------------

def shippable(row: dict, verdicts: dict) -> bool:
    """A row is shown only when it matched on every check and a person
    has kept it. Either one alone is not enough."""
    key = row.get("key")
    verdict = (verdicts.get(key) or {}).get("verdict") if key else None
    return row.get("status") == "matched" and verdict == "keep"


def board(shipped: dict, verdicts: dict, live: dict[str, dict] | None = None) -> dict:
    """What /v1/polymarket serves: the kept rows, by episode, newest first,
    with each market's standing brought up to date when it could be.

    Only what a reader needs leaves here. The OCR text, the candidates that
    were turned down and the token ids stay in the shipped file, where a
    person checking a row can see them.
    """
    live = live or {}
    episodes = []
    # The tally counts a market once, at the odds of the first show it was
    # on: Apple's "largest company on October 31" ran on 22 Oct at 30% and
    # on 29 Oct at 37%, and counting it twice would count one result twice.
    tally: dict[str, tuple[str, int, str | None]] = {}
    for ep in shipped.get("episodes") or []:
        rows = []
        for row in ep.get("rows") or []:
            if not shippable(row, verdicts):
                continue
            market = row["market"]
            standing = live.get(str(market["id"])) or row.get("standing") or {}
            shown = row["first"]["yes"]
            lean = None if shown == 50 else ("yes" if shown > 50 else "no")
            settled = standing.get("result") if standing.get("state") == "resolved" else None
            mid, aired = str(market["id"]), row["first"]["aired"]
            if mid not in tally or aired < tally[mid][0]:
                tally[mid] = (aired, shown, settled)
            rows.append({
                "key": row["key"],
                "question": market["question"],
                "url": market.get("url"),
                "event_title": market.get("event_title"),
                "first": {"t": row["first"]["t"], "aired": row["first"]["aired"], "yes": shown},
                "seen_from": (row.get("seen_from") or row["first"])["aired"],
                "last": {"aired": row["last"]["aired"]},
                "readings": row.get("readings"),
                "price_then": row.get("price_then"),
                "standing": standing,
                "leaned": lean,
                "went_as_leaned": None if not (settled and lean) else lean == settled,
                # A moment early, so the market is still coming into view.
                "watch": f"{ep['broadcast']}?t={max(0, int(row['first']['t']) - 2)}",
            })
        if rows:
            rows.sort(key=lambda r: r["first"]["t"])
            episodes.append({"episode_id": ep["episode_id"], "title": ep.get("title"),
                             "aired": ep.get("aired"), "broadcast": ep.get("broadcast"),
                             "rows": rows})
    episodes.sort(key=lambda e: e.get("aired") or "", reverse=True)
    settled = [(shown, result) for _, shown, result in tally.values() if result]
    leaning = [(shown, result) for shown, result in settled if shown != 50]
    return {
        "episodes": episodes,
        "tally": {"episodes": len(episodes),
                  "markets": len(tally),
                  "shown": sum(len(e["rows"]) for e in episodes),
                  "resolved": len(settled), "leaned": len(leaning),
                  "went_as_leaned": sum((shown > 50) == (result == "yes")
                                        for shown, result in leaning)},
        "built_at": shipped.get("built_at"),
        "checked_at": shipped.get("checked_at"),
    }

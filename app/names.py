"""Spell the names right, even when the captions do not.

Whisper mishears the words this archive is most about. Measured across
every transcript:

    Solana      → "Salana"        430 of 1,131   (39%)
    Polymarket  → "poly market"   151 of 268     (56%)
    Hyperliquid → "Hyperlid"      149, plus "hyper liquid" 80  (54%)
    pump.fun    → "pumpfun"        43 of 44      (98%)
    friend.tech → "Frentech"       14 of 14     (100%)
    Mt Gox      → "Mount Gox"       9 of 9      (100%)

Two consequences, and the second is the one people see.

Retrieval mostly survives it -- the embeddings match on meaning, which is
why a search for "friend.tech" finds "Frentech" and a search for Mt Gox
found "Mount Gox". What cannot survive it is the exact-token index, which
matches letters and so has holes in precisely the places Whisper failed.

And when the model quotes a mangled line, the error goes out under a real
person's name: "Hyperlid briefly flipped Salana price" was posted as
FaZe Banks' words. He said Hyperliquid and Solana. Reproducing the
transcription error IS the misquote, so correcting it is the faithful
thing rather than the liberty.

Only unambiguous manglings are here. "per" is 170 hits and almost always
the English word rather than PURR; "Seoul" is 13 hits and a real city as
often as it is SOL. Both are left alone -- a wrong correction is worse
than an uncorrected error, because it is one this code chose.
"""

from __future__ import annotations

import re

# mangled form -> what was actually said. Ordered longest-first at
# compile time so "hyper liquid" is tried before "hyperlid".
_ALIASES: dict[str, str] = {
    "salana": "Solana",
    "salada": "Solana",
    "salona": "Solana",
    "hyperlid": "Hyperliquid",
    "hyper liquid": "Hyperliquid",
    "poly market": "Polymarket",
    # Counted in the transcripts before adding: 285 lines say "robin
    # hood", 126 say "pump fun", 35 say "micro strategy". Every one of
    # them was unreachable by exact search, because expand() only knows
    # the spellings listed here -- a search for "Robinhood" matched the
    # 129 lines that spell it correctly and missed the other 285.
    "robin hood": "Robinhood",
    "pump fun": "pump.fun",
    "micro strategy": "MicroStrategy",
    "pumpfun": "pump.fun",
    "frentech": "friend.tech",
    "fren tech": "friend.tech",
    "z cash": "Zcash",
    "mount gox": "Mt. Gox",
    "board ape": "Bored Ape",
    "corweave": "CoreWeave",
    "lucanets": "Luca Netz",
    "bull penn": "Bullpen",
    # Published before it was caught: a reply on 2026-09-22 read "a Vibu
    # post saying Solana does 65% of all on-chain activity", because the
    # transcript of Market Bubble #15 says "Vibu tweeted" at 35:47. He is
    # Vibhu, and the account had already written his handle correctly in
    # an earlier post -- so the archive contradicted itself in public over
    # a caption error nobody had corrected.
    "vibu": "Vibhu",
    # The hackathon this project is entered in. On MCG's 6 October stream
    # Bunny of ClawPump says the judges will take longer to announce the
    # winner, and the transcript has him saying "the results of Anthem
    # Hack" and, a line later, "answer hack it's over now". Asked when the
    # AnsemHack winner would be announced, the archive answered from an
    # August episode and said nobody had given a date.
    "anthem hack": "AnsemHack",
    "answer hack": "AnsemHack",
}

# Manglings that are only manglings in context. Whisper writes ZEC as
# "Zeke", and "Zeke" is also what Banks calls Ansem: "I'll let Zeke
# introduce him", "Zeke gave you quite an intro". Of the 18 lines in the
# Market Bubble transcripts, 15 are the coin and 3 are the man, so a plain
# alias would put a ticker in place of a host's name. These fire only when
# the word sits next to market language, and leave the nickname alone.
#
# The cost of not doing this, 2026-10-02: asked "did ansem say zcash could
# go to 10,000", the site answered that he did not, with the line "isn't it
# like Zeke to 10,000, Hype to 1,000, Pump to 20 billion?" -- "that is
# exactly what I'm saying" -- among its own hits, the day after the account
# posted that exact moment.
_COIN_WORDS = r"(?:USD|USDT|BTC|price|chart|bags?|holders?|position|treasury|ETF)"
_CONTEXTUAL: list[tuple[re.Pattern, str]] = [
    # "Zeke to 10,000", "Zeke BTC", "Zeke price"
    (re.compile(rf"\bZeke(?=\s+(?:to\s+\$?\d|{_COIN_WORDS}\b))", re.IGNORECASE), "ZEC"),
    # "some Zeke", "a position in Zeke", "the narrative around Zeke"
    (re.compile(r"\b(some|in|around|bought|buying|long|short|shorting|sold|selling)"
                r"\s+Zeke\b(?!')", re.IGNORECASE), r"\1 ZEC"),
    # "BTC and Zeke", "Hype or Zeke"
    (re.compile(r"\b(BTC|Bitcoin|ETH|Ethereum|Hype|SOL|Solana)\s+(and|or|vs\.?|versus)"
                r"\s+Zeke\b", re.IGNORECASE), r"\1 \2 ZEC"),
]
# What a search for the coin should also look for in the exact-token index.
_EXPAND_ALSO = {"zec": ["zeke"], "zcash": ["zeke"],
                # People type it as two words as often as one.
                "ansem hackathon": ["anthem hack", "answer hack"],
                "ansem hack": ["anthem hack", "answer hack"],
                "ansemhack": ["ansem hack", "ansem hackathon"]}

_ALTERNATION = "|".join(
    re.escape(k) for k in sorted(_ALIASES, key=len, reverse=True))
_PATTERN = re.compile(rf"\b(?:{_ALTERNATION})\b", re.IGNORECASE)


def _cased(mangled: str, correct: str) -> str:
    """Keep SHOUTING when the source was shouting, otherwise use the
    canonical spelling. A transcript in caps should not force "SOLANA"
    into prose, but neither should it be silently lowercased."""
    return correct.upper() if mangled.isupper() and len(mangled) > 3 else correct


def fix(text: str) -> tuple[str, list[str]]:
    """Correct known caption manglings.

    Returns the text and what was changed, for the log. Text with nothing
    to fix comes back unchanged, so this is safe to run on everything.
    """
    if not text:
        return text, []
    changed: list[str] = []

    def swap(found: re.Match) -> str:
        was = found.group(0)
        right = _cased(was, _ALIASES[was.lower()])
        if was != right:
            changed.append(f"{was!r}->{right!r}")
        return right

    text = _PATTERN.sub(swap, text)
    for pattern, replacement in _CONTEXTUAL:
        def contextual(found: re.Match, replacement: str = replacement) -> str:
            right = found.expand(replacement)
            changed.append(f"{found.group(0)!r}->{right!r}")
            return right
        text = pattern.sub(contextual, text)
    return text, changed


def phrases() -> list[str]:
    """Every spelling here that is more than one word.

    For the exact-token index, which keeps words and so cannot tell these
    apart from their halves. "Anthem" is in 65 windows of the MCG archive,
    most of them $ANSEM misheard, and "hack" is in too many to be kept at
    all. The window that says "Anthem Hack" was one of the 65, and a lookup
    that takes eight took eight others.
    """
    found = [m for m in _ALIASES if " " in m]
    found += [s for spellings in _EXPAND_ALSO.values()
              for s in spellings if " " in s]
    return list(dict.fromkeys(found))


def expand(query: str) -> list[str]:
    """The query plus the spellings the transcripts actually use.

    For the exact-token index, which matches letters rather than meaning
    and therefore misses every line Whisper got wrong. Somebody searching
    "solana" should still reach the four hundred lines that say "Salana".
    """
    lower = (query or "").lower()
    out = []
    for mangled, correct in _ALIASES.items():
        if correct.lower() in lower and mangled not in lower:
            out.append(re.sub(re.escape(correct), mangled, query,
                              flags=re.IGNORECASE))
    for word, spellings in _EXPAND_ALSO.items():
        if re.search(rf"\b{word}\b", lower):
            out.extend(re.sub(rf"\b{word}\b", s, query, flags=re.IGNORECASE)
                       for s in spellings if s not in lower)
    return out

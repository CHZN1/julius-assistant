"""Spoken money amounts, parsed exactly, for spending limits.

"50 cents", "$0.50", "half a dollar", "a dollar fifty", "2 bucks" and "two and a half dollars" each mean one amount. A
whole number with no unit ("limit it to 50") could be dollars or cents, so it comes back ambiguous and the caller asks.
A decimal with no unit ("1.50") is dollars.
"""

import re
from dataclasses import dataclass

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_WORD_VALUE = {w: i for i, w in enumerate(_ONES)} | {w: 20 + 10 * i for i, w in enumerate(_TENS)}

_S = r"[\s-]+"
_WORD = (
    r"(?:(?:"
    + "|".join(_TENS)
    + r")(?:[\s-](?:"
    + "|".join(_ONES[1:10])
    + r"))?|"
    + "|".join(sorted(_ONES, key=len, reverse=True))
    + r")\b"
)
_INT = rf"(?:\d+\b|{_WORD})"
_NUM = rf"(?:\d*\.\d+\b|\d+\b|{_WORD})"
_DOLLAR = r"(?:dollars?|bucks?|usd)\b"
_CENT = r"(?:cents?\b|¢)"
_FOLLOWS_BARE = re.compile(
    r"\s*(?:$|[,.;!?]|(?:in|on|and|please|for|limit|cap|budget|max)\b)", re.I
)

# Tried in order at one position; the first that matches wins.
_FORMS = [
    ("half", re.compile(rf"half{_S}(?:a|one){_S}(?:dollar|buck)\b", re.I)),
    ("sign", re.compile(rf"\$\s?(?P<d>\d*\.?\d+)(?:{_S}{_DOLLAR})?", re.I)),
    ("and_half", re.compile(rf"(?P<d>{_INT}){_S}and{_S}a{_S}half{_S}{_DOLLAR}", re.I)),
    (
        "dollars",
        re.compile(
            rf"(?P<d>{_NUM}|an?\b){_S}{_DOLLAR}"
            rf"(?:,?{_S}(?:and{_S})?(?P<c1>{_INT}){_S}{_CENT}|{_S}(?P<c2>{_INT})(?!{_S}{_CENT}))?",
            re.I,
        ),
    ),
    ("cents", re.compile(rf"(?P<c>{_INT})\s*{_CENT}", re.I)),
    ("bare", re.compile(rf"(?P<n>{_NUM})", re.I)),
]


@dataclass
class Amount:
    value: float | None  # dollars; None when ambiguous
    start: int
    end: int
    ambiguous: bool = False
    number: float | None = None  # the bare number, when ambiguous


def _number(s: str) -> float | None:
    s = s.strip().casefold()
    if s in ("a", "an"):
        return 1.0
    if re.fullmatch(r"\d*\.?\d+", s):
        return float(s)
    parts = re.split(r"[\s-]+", s)
    if all(p in _WORD_VALUE for p in parts) and parts:
        return float(sum(_WORD_VALUE[p] for p in parts))
    return None


def _whole_under_100(s: str) -> int | None:
    n = _number(s)
    return int(n) if n is not None and n == int(n) and 0 <= n < 100 and "." not in s else None


def parse_amount(text: str, pos: int = 0) -> Amount | None:
    """The amount starting exactly at text[pos], or None."""
    for kind, form in _FORMS:
        m = form.match(text, pos)
        if not m:
            continue
        if kind == "half":
            return Amount(0.5, m.start(), m.end())
        if kind == "sign":
            return Amount(float(m.group("d")), m.start(), m.end())
        if kind == "and_half":
            return Amount(_number(m.group("d")) + 0.5, m.start(), m.end())
        if kind == "dollars":
            d = _number(m.group("d"))
            extra = m.group("c1") or m.group("c2")
            if extra is not None:
                cents = _whole_under_100(extra)
                if cents is None or "." in m.group("d"):  # "1.5 dollars fifty" or "a dollar 150"
                    return Amount(d, m.start(), m.start("c1" if m.group("c1") else "c2"))
                return Amount(round(d + cents / 100, 2), m.start(), m.end())
            return Amount(d, m.start(), m.end())
        if kind == "cents":
            c = _number(m.group("c"))
            return Amount(round(c / 100, 4), m.start(), m.end())
        n = m.group("n")
        if "." in n:
            return Amount(float(n), m.start(), m.end())
        return Amount(None, m.start(), m.end(), ambiguous=True, number=_number(n))
    return None


# A limit phrase, then the amount. After a weak lead ("with", "for", "up to") only an amount with a unit counts, so
# "with 2 cases" and "up to 3 times" stay out. After a strong lead, a bare whole number counts only when nothing
# that looks like a noun follows it ("limit it to 2 files" is not money).
_STRONG = re.compile(
    r"\b(?:limit(?:ed)?(?:\s+it)?\s+to|budget(?:\s+of|\s+is)?|cap(?:ped)?(?:\s+it)?\s+at|spend(?:\s+up\s+to|\s+at\s+most|\s+no\s+more\s+than)?|at\s+most|no\s+more\s+than|max(?:imum)?(?:\s+of)?)\s+",
    re.I,
)
_WEAK = re.compile(r"\b(?:with|for|up\s+to)\s+", re.I)
_TRAIL = re.compile(r"\s+(?:limit|cap|budget|max)\b", re.I)
_A = re.compile(r"an?\s+", re.I)


def find_cap(text: str) -> Amount | None:
    """The spending limit in a request, its span covering the limit phrase, or None. ambiguous=True means ask."""
    found = []
    for lead, strong in ((_STRONG, True), (_WEAK, False)):
        for m in lead.finditer(text):
            a = parse_amount(text, m.end())
            if a is None and (skip := _A.match(text, m.end())):  # "with a 3 dollar limit"
                a = parse_amount(text, skip.end())
            if a is None:
                continue
            if a.ambiguous:
                if not strong or not _FOLLOWS_BARE.match(text, a.end):
                    continue
            elif not strong and _is_unitless(text, a):
                continue
            t = _TRAIL.match(text, a.end)
            found.append(Amount(a.value, m.start(), t.end() if t else a.end, a.ambiguous, a.number))
    return min(found, key=lambda a: a.start) if found else None


def _is_unitless(text: str, a: Amount) -> bool:
    s = text[a.start : a.end]
    return not re.search(rf"\$|{_DOLLAR}|{_CENT}|half", s, re.I)

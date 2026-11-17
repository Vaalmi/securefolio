"""The figures in an answer's sentences, checked against the passages each sentence
cites. A figure is shown when a cited passage holds the same value, worked out when
code computed it, and unshown otherwise. The check marks; it never rewrites. Pure
functions: nothing here touches a store or a model."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

MONEY, DATE, PERCENT, NUMBER, UNIT = "money", "date", "percent", "number", "unit"
KINDS = (MONEY, DATE, PERCENT, NUMBER, UNIT)
SHOWN, UNSHOWN, WORKED = "shown", "unshown", "worked"
UNSHOWN_WORDS = "(not in its sources)"
UNCITED_WORDS = "(uncited)"
BIG = Decimal(100)  # a number with no unit is checked from here up
LAST_MONTH, LAST_DAY = 12, 31

MONTHS = {
    name: n
    for n, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
ONES = {
    word: n
    for n, word in enumerate(
        [
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
    )
}
TENS = {
    word: 10 * n
    for n, word in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"],
        start=2,
    )
}
SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9}
SHORT_SCALES = {"k": 1000, "m": 10**6, "mm": 10**6, "bn": 10**9, "b": 10**9}
# "$" is read as US dollars, so "CAD 1,200" never shows "$1,200".
CURRENCIES = {
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "dollars": "USD",
    "dollar": "USD",
    "c$": "CAD",
    "ca$": "CAD",
    "cad": "CAD",
    "a$": "AUD",
    "au$": "AUD",
    "aud": "AUD",
    "nz$": "NZD",
    "nzd": "NZD",
    "€": "EUR",
    "eur": "EUR",
    "euros": "EUR",
    "euro": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "chf": "CHF",
    "inr": "INR",
    "₹": "INR",
}
UNITS = (
    "days?|weeks?|months?|years?|hours?|hrs?|minutes?|mins?|seconds?|"
    "nights?|business days?|working days?|calendar days?|miles?|mi|"
    "kilometers?|kilometres?|km|kilograms?|kg|lbs?|feet|foot|ft|inches|inch|"
    "meters?|metres?|cm|mm|liters?|litres?|gallons?|gal|acres?|sq\\.? ?ft|"
    "square (?:feet|foot|meters?|metres?)|payments?|installments?|instalments?|"
    "visits?|sessions?|units?|people|persons?|employees?|pages?|copies"
)

WORD = "|".join(sorted([*ONES, *TENS], key=len, reverse=True))
# Words, each group with any scale after it, as one figure: "thirty-five", "two
# hundred thirty", "two thousand five hundred", "one hundred and twenty-five".
GROUP = rf"(?:{WORD})(?:[- ](?:{WORD}))?"
SPELLED = rf"{GROUP}(?: (?:hundred|thousand|million|billion)(?:(?: and)? {GROUP})?)*"
DIGITS = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
AMOUNT = rf"(?:{DIGITS})(?:\s?(?:million|billion|thousand|bn|mm|[kmb])\b)?"
# Any number takes a scale word after it, as money does: "1.2 million people" is
# 1,200,000. Not a letter, which can be a unit: "5 m" is five metres.
SCALED = rf"(?:{DIGITS})(?:\s(?:thousand|million|billion)\b)?"
SYMBOL = r"(?:US\$|CA\$|AU\$|NZ\$|C\$|A\$|\$|€|£|₹)"
MINUS = "-\u2212\u2013"  # a hyphen, a minus sign, an en dash
CODE = r"(?:USD|CAD|AUD|NZD|EUR|GBP|CHF|INR)"
AFTER = r"(?:(?:USD|CAD|AUD|NZD|EUR|GBP|CHF|INR|dollars?|euros?)\b|[$€£₹])"
MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
DAY = r"(?:[12]\d|3[01]|0?[1-9])(?:st|nd|rd|th)?"
YEAR = r"(?:1[89]\d\d|2[01]\d\d)"

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iso", re.compile(rf"\b{YEAR}-[01]\d-[0-3]\d\b")),
    ("month_day", re.compile(rf"\b{MONTH} {DAY},? {YEAR}\b", re.I)),
    ("day_month", re.compile(rf"\b{DAY} (?:of )?{MONTH},? {YEAR}\b", re.I)),
    ("numeric", re.compile(rf"\b(?:[0-3]?\d)[/.-](?:[0-3]?\d)[/.-]{YEAR}\b")),
    ("month_year", re.compile(rf"\b{MONTH},? {YEAR}\b", re.I)),
    # A code may come before a symbol, "CAD $1,200", or after the amount, "$1,200 CAD":
    # either way the amount is read with the code's currency, never as US dollars.
    (
        "money_before",
        re.compile(
            rf"(?:\b{CODE}\s?(?:[{MINUS}]\s?)?(?:{SYMBOL}\s?)?|{SYMBOL}\s?)"
            rf"(?:[{MINUS}]\s?)?(?:{AMOUNT})"
            rf"(?:\s?{CODE}\b)?",
            re.I,
        ),
    ),
    ("money_after", re.compile(rf"\b(?:{AMOUNT})\s?{AFTER}", re.I)),
    (
        "percent",
        re.compile(
            rf"(?:\b(?:{DIGITS})|\b{SPELLED})\s?(?:%|percent\b|per cent\b)", re.I
        ),
    ),
    ("unit", re.compile(rf"(?:\b(?:{SCALED})|\b{SPELLED})[- ](?:{UNITS})\b", re.I)),
    ("number", re.compile(rf"(?<![\w.,])(?:{SCALED})(?![\w])|\b{SPELLED}\b", re.I)),
)


@dataclass(frozen=True)
class Figure:
    """A figure as written, its kind and its value, and where it is in its text."""

    text: str
    kind: str
    value: Any  # Decimal, (currency, Decimal), or a set of (year, month, day | None)
    start: int
    end: int


@dataclass(frozen=True)
class Passage:
    """What a citation gave the model: its passage's words, and its document's title."""

    text: str
    title: str = ""


def _number(text: str) -> Decimal | None:
    """Digits, with or without thousands separators, or words, "thirty-five" or
    "two hundred"; a scale after it, "1.2 million" or "$5k", multiplies it."""
    said = text.strip().lower()
    scale = 1
    found = re.search(r"\s?(million|billion|thousand|bn|mm|[kmb])$", said)
    if found and re.match(rf"^(?:{DIGITS})", said):
        word = found.group(1)
        scale = SCALES.get(word) or SHORT_SCALES[word]
        said = said[: found.start()].strip()
    if re.fullmatch(DIGITS, said):
        try:
            return Decimal(said.replace(",", "")) * scale
        except InvalidOperation:
            return None
    return _spelled(said)


def _spelled(said: str) -> Decimal | None:
    """Words as one number: "hundred" scales the group before it, and "thousand" and
    up close it, so "two thousand five hundred" is 2,500."""
    total, group = 0, 0
    for word in re.split(r"[-\s]+", said):
        if word in ONES:
            group += ONES[word]
        elif word in TENS:
            group += TENS[word]
        elif word == "hundred":
            group = max(group, 1) * SCALES[word]
        elif word in SCALES:
            total += max(group, 1) * SCALES[word]
            group = 0
        elif word != "and":
            return None
    return Decimal(total + group)


def _date(kind: str, text: str) -> frozenset[tuple[int, int, int | None]]:
    """Every reading of a date: 03/04/2025 is March 4 or April 3, and a month and year
    alone has no day."""
    words = re.findall(r"[A-Za-z]+|\d+", text)
    numbers = [int(w) for w in words if w.isdigit()]
    month = next((MONTHS[w.lower()] for w in words if w.lower() in MONTHS), None)
    if kind == "iso":
        return frozenset({(numbers[0], numbers[1], numbers[2])})
    if kind == "numeric":
        first, second, year = numbers
        readings = {(year, first, second), (year, second, first)}
        return frozenset(
            r for r in readings if r[1] <= LAST_MONTH and r[2] and r[2] <= LAST_DAY
        )
    if kind == "month_year":
        return frozenset({(numbers[-1], month or 0, None)})
    day = next(n for n in numbers if n <= LAST_DAY)
    return frozenset({(numbers[-1], month or 0, day)})


def _currency(text: str) -> str:
    lowered = text.lower()
    for mark in sorted(CURRENCIES, key=len, reverse=True):
        if mark in lowered:
            return CURRENCIES[mark]
    return "USD"


def _amount_of(text: str) -> Decimal | None:
    found = re.search(AMOUNT, text, re.I)
    return _number(found.group(0)) if found else None


def _money(text: str) -> tuple[str, Any] | None:
    """An amount with its currency. A minus anywhere among the marks before its
    digits is the amount's: "CAD -$1,200", "$-1,200" and "USD -1,200" are all minus
    1,200 in their currency. One before the first mark is `_signed`'s."""
    amount = _amount_of(text)
    if amount is None:
        return None
    marks = re.match(r"\D*", text)
    if marks and any(sign in marks.group(0) for sign in MINUS):
        amount = -amount
    return (MONEY, (_currency(text), amount))


def _value(kind: str, text: str) -> tuple[str, Any] | None:
    if kind in ("iso", "month_day", "day_month", "numeric", "month_year"):
        readings = _date(kind, text)
        return (DATE, readings) if readings else None
    if kind in ("money_before", "money_after"):
        return _money(text)
    if kind == "percent":
        said = re.sub(r"\s?(?:%|percent|per cent)$", "", text.strip(), flags=re.I)
        number = _number(said)
        return (PERCENT, number) if number is not None else None
    if kind == "unit":
        said = re.match(rf"(?:{SCALED})|{SPELLED}", text, re.I)
        number = _number(said.group(0)) if said else None
        if number is None:
            return None
        # "36,000 miles" is a number of 100 or more; only a smaller one needs its unit.
        return (NUMBER if number >= BIG else UNIT, number)
    number = _number(text)
    return (NUMBER, number) if number is not None else None


SIGNED = (MONEY, PERCENT, NUMBER, UNIT)


def _signed(text: str, kind: str, value: Any, start: int) -> tuple[Any, int]:
    """A minus sign just before a figure, not joining it to a word or a number before
    it, makes it negative: "-$1,200" is not "$1,200", but "$100-$200" is two amounts."""
    before = start - 1
    if kind not in SIGNED or before < 0 or text[before] not in MINUS:
        return value, start
    if before > 0 and text[before - 1].isalnum():
        return value, start
    if kind == MONEY:
        return (value[0], -value[1]), before
    return -value, before


def figures_in(text: str) -> list[Figure]:
    """Every figure in a text, the longest reading first: a date is never also read as
    its day and year, nor an amount as its bare number. A figure keeps its sign."""
    taken: list[tuple[int, int]] = []
    found: list[Figure] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < b and a < end for a, b in taken):
                continue
            read = _value(kind, match.group(0))
            if read is None:
                continue
            value, start = _signed(text, read[0], read[1], start)
            taken.append((start, end))
            found.append(Figure(text[start:end], read[0], value, start, end))
    return sorted(found, key=lambda f: f.start)


def checked(figure: Figure) -> bool:
    """The kinds the check covers: money, dates, percentages, a number of 100 or more,
    and a smaller number with a unit. A smaller bare number isn't checked; a
    negative one is measured by its size, so "-120 days" is checked."""
    return figure.kind != NUMBER or abs(figure.value) >= BIG


def _numbers_in(figure: Figure) -> set[Decimal]:
    """The plain numbers a passage's figure holds, for a sentence's number to match:
    a date's day and year, an amount, a number with its unit."""
    if figure.kind == DATE:
        return {Decimal(part) for reading in figure.value for part in reading if part}
    if figure.kind == MONEY:
        return {figure.value[1]}
    if figure.kind in (NUMBER, UNIT):
        return {figure.value}
    return set()


def _matches(wanted: Figure, have: Figure) -> bool:
    if wanted.kind == MONEY:
        if have.kind == MONEY:
            return have.value == wanted.value
        # An amount the passage gives with no currency: "1200.00" shows "$1,200".
        return have.kind in (NUMBER, UNIT) and have.value == wanted.value[1]
    if wanted.kind == DATE:
        if have.kind != DATE:
            return False
        return any(
            (y, m) == (hy, hm) and (d is None or d == hd)
            for y, m, d in wanted.value
            for hy, hm, hd in have.value
        )
    if wanted.kind == PERCENT:
        return have.kind == PERCENT and have.value == wanted.value
    # A number, or a smaller one with a unit: units aren't checked, the number is.
    return wanted.value in _numbers_in(have)


def _once(wanted: Figure, passage: str) -> tuple[int, int] | None:
    """Where in the passage the figure is, when it is there exactly once."""
    at = [have for have in figures_in(passage) if _matches(wanted, have)]
    return (at[0].start, at[0].end) if len(at) == 1 else None


TOKEN = re.compile(r"[\w$€£₹.,/-]+")


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [
        (m.group(0).strip(".,").removesuffix("'s").lower(), m.start(), m.end())
        for m in TOKEN.finditer(text)
    ]


def _in_title(figure: Figure, sentence: str, title: str) -> bool:
    """A title's figure counts only where the sentence names that document, its words
    together around the figure in any order, with one word between at most: "your
    2024 federal return" names "Federal return 2024", but "the lease expires in 2025"
    doesn't name "2025 Lease"."""
    named = [word for word, _, _ in _tokens(title)]
    if not named or not any(_matches(figure, have) for have in figures_in(title)):
        return False
    words = _tokens(sentence)
    at = [
        i
        for i, (_, start, end) in enumerate(words)
        if start < figure.end and figure.start < end
    ]
    if not at:
        return False
    for size in range(len(named), len(named) + 2):
        for first in range(max(0, at[0] - size + 1), at[0] + 1):
            window = [word for word, _, _ in words[first : first + size]]
            if all(window.count(word) >= named.count(word) for word in named):
                return True
    return False


def check_sentence(
    text: str,
    cited: dict[int, Passage],
    worked: list[Decimal] | None = None,
) -> list[dict[str, Any]]:
    """Each checked figure of one cited sentence: shown, with the citation whose passage
    shows it and where, when it's there exactly once; worked out by code; or unshown."""
    results = []
    for figure in figures_in(text):
        if not checked(figure):
            continue
        result: dict[str, Any] = {
            "text": figure.text,
            "kind": figure.kind,
            "start": figure.start,
            "end": figure.end,
        }
        amount = figure.value[1] if figure.kind == MONEY else figure.value
        # A list's total is worked out in dollars: an amount in another currency
        # isn't it, however the digits compare.
        dollars = figure.kind == NUMBER or (
            figure.kind == MONEY and figure.value[0] == "USD"
        )
        if worked and dollars and amount in worked:
            results.append({**result, "state": WORKED})
            continue
        shown = next(
            (
                n
                for n, passage in cited.items()
                if any(_matches(figure, have) for have in figures_in(passage.text))
                or _in_title(figure, text, passage.title)
            ),
            None,
        )
        if shown is None:
            results.append({**result, "state": UNSHOWN})
            continue
        at = _once(figure, cited[shown].text)
        found = cited[shown].text[at[0] : at[1]] if at else None
        results.append(
            {
                **result,
                "state": SHOWN,
                "cite": shown,
                "at": list(at) if at else None,
                "found": found,  # the passage's own spelling, to place it by
            }
        )
    return results


def check(
    parts: list[dict[str, Any]],
    passages: dict[int, Passage],
    worked: list[Decimal] | None = None,
) -> list[list[dict[str, Any]]]:
    """The figures of every part of an answer, in order. An uncited sentence's figures
    aren't checked: it carries its own mark, and has no passage to check against. A
    citation whose passage isn't known leaves its sentence unchecked: what that passage
    shows can't be told."""
    checked_parts = []
    for part in parts:
        cites = part.get("cite") or []
        known = bool(cites) and all(n in passages for n in cites)
        cited = {n: passages[n] for n in cites} if known else {}
        checked_parts.append(
            check_sentence(part.get("text", ""), cited, worked) if known else []
        )
    return checked_parts


def worked_of(table: dict[str, Any] | None) -> list[Decimal]:
    """What code worked out for an answer: a list's total, in dollars, as the
    answer's table gives it."""
    if not table or not table.get("wants_total") or table.get("total") is None:
        return []
    return [Decimal(str(table["total"])).quantize(Decimal("0.01"))]

"""Spoken-English normalisation for comparing a script with an independent transcript.

Notation differences are folded together:
    one hundred / 100 / a hundred, three point five / 3.5, twenty-five / 25, fifteen hundred / 1,500,
    twenty twenty-six / 2026, nineteen oh five / 1905, nine thirty / 9:30, twenty-first / 21st,
    fifty percent / 50%, twelve dollars / $12, can't / can not / cannot.
Meaning differences stay visible: every number is reduced to one canonical token ("#1500", "#3.5",
"#21st"), so a different value, a missing number or a missing negation still shows up.

Known limit (fails safe): two separate spoken numbers next to each other ("two twenty-dollar tickets")
are read like a clock time / year ("2:20" -> #220). If the other side writes them differently the check
reports a mismatch and the clip goes to a person instead of passing silently.
"""

from __future__ import annotations

import re

UNITS = {
    "zero": 0,
    "oh": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}
TEENS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
ORD_UNITS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
}
ORD_TEENS = {
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
}
ORD_TENS = {
    "twentieth": 20,
    "thirtieth": 30,
    "fortieth": 40,
    "fiftieth": 50,
    "sixtieth": 60,
    "seventieth": 70,
    "eightieth": 80,
    "ninetieth": 90,
}
ORD_OTHER = {"hundredth": 100, "thousandth": 1000}
NEGATIONS = {
    "not",
    "no",
    "never",
    "neither",
    "nor",
    "none",
    "nobody",
    "nothing",
    "nowhere",
    "without",
    "hardly",
    "barely",
    "rarely",
    "seldom",
}
CONTRACTIONS = [
    (r"\bcan't\b", "can not"),
    (r"\bwon't\b", "will not"),
    (r"\bshan't\b", "shall not"),
    (r"\bain't\b", "is not"),
    (r"n't\b", " not"),
    (r"\bcannot\b", "can not"),
]
NUMBER_WORDS = (
    set(UNITS)
    | set(TEENS)
    | set(TENS)
    | set(SCALES)
    | {"hundred"}
    | set(ORD_UNITS)
    | set(ORD_TEENS)
    | set(ORD_TENS)
    | set(ORD_OTHER)
)


def _num(v: float | int) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"#{v}"


def _ordinal(n: int) -> str:
    return f"#{n}st"  # one suffix for every ordinal: only the value matters


def _pre(text: str) -> list[str]:
    t = text.lower().replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", " ")
    for pat, rep in CONTRACTIONS:
        t = re.sub(pat, rep, t)
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 1,500 -> 1500
    t = re.sub(r"\$\s*(\d+(?:\.\d+)?)", r"\1 dollars", t)
    t = re.sub(r"£\s*(\d+(?:\.\d+)?)", r"\1 pounds", t)
    t = re.sub(r"€\s*(\d+(?:\.\d+)?)", r"\1 euros", t)
    t = re.sub(r"¥\s*(\d+(?:\.\d+)?)", r"\1 yen", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"\bper cent\b", "percent", t)
    t = re.sub(r"\b(a\.m\.|am)\b", " am ", t)
    t = re.sub(r"\b(p\.m\.|pm)\b", " pm ", t)
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)  # twenty-five -> twenty five
    t = re.sub(r"[^a-z0-9.:'\s]", " ", t)
    out = []
    for raw in t.split():
        w = raw.strip(".:'")
        w = re.sub(r"'s$", "s", w).replace("'", "")
        if w:
            out.append(w)
    return out


def _digit_token(w: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", w)
    if m:  # clock time 9:30 -> #930, 10:05 -> #1005 (same as "nine thirty", "ten oh five")
        h, mi = int(m.group(1)), int(m.group(2))
        return _num(h * 100 + mi) if mi else _num(h)
    m = re.fullmatch(r"(\d+)(st|nd|rd|th)", w)
    if m:
        return _ordinal(int(m.group(1)))
    if re.fullmatch(r"\d+(\.\d+)?", w):
        return _num(float(w) if "." in w else int(w))
    return None


def normalize_tokens(text: str) -> list[str]:
    """Words, with every number replaced by one canonical '#value' token."""
    words = _pre(text)
    out: list[str] = []
    spoken: list[bool] = []  # was this number token built from words (eligible for year / clock pairing)?
    i = 0
    while i < len(words):
        w = words[i]
        d = _digit_token(w)
        if d is not None:
            out.append(d)
            spoken.append(False)
            i += 1
            continue
        is_article = w == "a" and i + 1 < len(words) and words[i + 1] in ("hundred", "thousand", "million", "billion")
        if (
            w not in NUMBER_WORDS
            or (w == "oh" and not (out and out[-1].startswith("#")))
            or (w == "second" and not _ordinal_context(words, i))
        ):
            if not is_article:
                out.append(w)
                spoken.append(False)
                i += 1
                continue
        j, value, ordinal = _parse_number(words, i)
        if j == i:  # nothing parsed
            out.append(w)
            spoken.append(False)
            i += 1
            continue
        if words[j - 1] == "oh" and value == 0 and out and out[-1].startswith("#") and spoken[-1]:
            # "nineteen oh five" / "ten oh five": X oh Y
            if j < len(words) and words[j] in UNITS:
                j2, y, _ = _parse_number(words, j)
                prev = int(out[-1][1:])
                out[-1] = _num(prev * 100 + y)
                spoken[-1] = False
                i = j2
                continue
        tok = _ordinal(int(value)) if ordinal else _num(value)
        # "twenty twenty-six" -> 2026, "nine thirty" -> 930: two spoken numbers in a row
        if not ordinal and out and spoken and spoken[-1] and out[-1].startswith("#") and not out[-1].endswith("st"):
            prev = float(out[-1][1:])
            if prev.is_integer() and 1 <= prev <= 99 and isinstance(value, int) and 10 <= value <= 99:
                out[-1] = _num(int(prev) * 100 + value)
                spoken[-1] = False
                i = j
                continue
        out.append(tok)
        spoken.append(not ordinal and isinstance(value, int) and value < 100)
        i = j
    return out


def _ordinal_context(words: list[str], i: int) -> bool:
    """'second' is an ordinal unless it is clearly the unit of time ('a second', 'one second', 'seconds')."""
    prev = words[i - 1] if i else ""
    return prev not in ("a", "one", "per", "split", "just")


def _parse_number(words: list[str], i: int) -> tuple[int, float | int, bool]:
    """Parse a spoken number starting at i. Returns (next index, value, is_ordinal)."""
    total, current, last = 0, 0, None
    j = i
    seen = False
    while j < len(words):
        w = words[j]
        if (
            w == "a"
            and j + 1 < len(words)
            and words[j + 1] in ("hundred", "thousand", "million", "billion")
            and not seen
        ):
            current, last, seen = 1, "unit", True
            j += 1
            continue
        if w in UNITS:
            if w == "oh" and seen and last not in ("tens",):
                return j, total + current, False  # "nineteen oh ..." handled by caller
            if last in (None, "hundred", "scale", "and"):
                current += UNITS[w]
            elif last == "tens" and current % 10 == 0:
                current += UNITS[w]
            else:
                break
            last, seen = "unit", True
        elif w in TEENS:
            if last in (None, "hundred", "scale", "and"):
                current += TEENS[w]
            else:
                break
            last, seen = "teen", True
        elif w in TENS:
            if last in (None, "hundred", "scale", "and"):
                current += TENS[w]
            else:
                break
            last, seen = "tens", True
        elif w == "hundred" and seen and last in ("unit", "teen", "tens"):
            current = current * 100
            last = "hundred"
        elif w in SCALES and seen and last in ("unit", "teen", "tens", "hundred"):
            total += current * SCALES[w]
            current, last = 0, "scale"
        elif (
            w == "and"
            and last in ("hundred", "scale")
            and j + 1 < len(words)
            and words[j + 1]
            in (set(UNITS) | set(TEENS) | set(TENS) | set(ORD_UNITS) | set(ORD_TEENS) | set(ORD_TENS)) - {"oh"}
        ):
            last = "and"
        elif w == "point" and seen and j + 1 < len(words) and words[j + 1] in UNITS:
            k, digits = j + 1, ""
            while k < len(words) and words[k] in UNITS:
                digits += str(UNITS[words[k]])
                k += 1
            return k, float(f"{total + current}.{digits}"), False
        elif w in ORD_UNITS and (last in (None, "hundred", "scale", "and") or (last == "tens" and current % 10 == 0)):
            return j + 1, total + current + ORD_UNITS[w], True
        elif w in ORD_TEENS and last in (None, "hundred", "scale", "and"):
            return j + 1, total + current + ORD_TEENS[w], True
        elif w in ORD_TENS and last in (None, "hundred", "scale", "and"):
            return j + 1, total + current + ORD_TENS[w], True
        elif w in ORD_OTHER and seen:
            return (
                j + 1,
                (total + (current or 1)) * ORD_OTHER[w] if w == "hundredth" else total + (current or 1) * ORD_OTHER[w],
                True,
            )
        else:
            break
        j += 1
    if not seen:
        return i, 0, False
    if last == "and":  # trailing "and" belongs to the sentence
        j -= 1
    return j, total + current, False


def critical_tokens(tokens: list[str]) -> list[str]:
    """Numbers and negations, the words whose loss or change flips the meaning."""
    return sorted(t for t in tokens if t.startswith("#") or t in NEGATIONS)

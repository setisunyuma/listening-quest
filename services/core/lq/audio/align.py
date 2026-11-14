"""Word alignment between an expected text and what was heard (speech recognition) or typed (dictation).

Both checks used to compare loose summaries: dictation counted matched words only (extra words such as an
inserted "not" were free), and the audio check compared SORTED lists of numbers / negations (so "Anna paid 20,
Ben 30" read back as "Anna 30, Ben 20" passed). Here the two token lists are aligned position by position, and a
change at a key word - number (with sign), negation, weekday, month, direction, comparison, a contrasting verb,
or one name swapped for another name of the same script - is reported even when the overall error rate is low.

Deterministic and free: no model call. A speech recogniser can itself mishear, so a finding means "suspect", and
the caller decides between one re-synthesis and human review."""

from __future__ import annotations

import re

from lq.audio.textnorm import NEGATIONS, normalize_tokens

WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
MONTHS = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
DIRECTIONS = {"left", "right", "north", "south", "east", "west", "up", "down", "upstairs", "downstairs", "inside", "outside",
              "above", "below", "front", "behind", "before", "after", "first", "last", "next", "previous", "early", "late",
              "earlier", "later", "morning", "afternoon", "evening", "night", "today", "tomorrow", "yesterday"}  # fmt: skip
COMPARISONS = {"more", "less", "fewer", "most", "least", "better", "worse", "best", "worst", "higher", "lower", "larger",
               "smaller", "bigger", "longer", "shorter", "cheaper", "faster", "slower", "same", "different", "only",
               "all", "both", "either", "some", "half", "double", "twice", "always", "sometimes", "often", "usually"}  # fmt: skip
# verbs whose replacement by their counterpart reverses the meaning (stems; -s/-ed/-ing forms are matched)
CONTRAST_VERBS = {"leave", "arrive", "depart", "open", "close", "start", "begin", "finish", "end", "stop", "buy", "sell",
                  "accept", "reject", "refuse", "agree", "disagree", "increase", "decrease", "rise", "fall", "drop",
                  "win", "lose", "pass", "fail", "allow", "forbid", "ban", "add", "remove", "cancel", "postpone",
                  "borrow", "lend", "give", "take", "send", "receive", "come", "go", "push", "pull", "like", "dislike",
                  "love", "hate", "remember", "forget", "include", "exclude", "join", "quit"}  # fmt: skip


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            base = w[: -len(suf)]
            return base[:-1] if suf in ("ing", "ed") and len(base) > 3 and base[-1] == base[-2] else base
    return w


SENTENCE_STARTERS = {"the", "a", "an", "it", "i", "we", "you", "he", "she", "they", "there", "this", "that", "these",
                     "those", "what", "when", "where", "why", "how", "who", "which", "if", "so", "but", "and", "or",
                     "yes", "no", "oh", "well", "ok", "okay", "my", "your", "our", "his", "her", "their", "let", "please",
                     "thank", "thanks", "hello", "hi", "sorry", "excuse", "question", "number", "do", "does", "did",
                     "is", "are", "was", "were", "can", "could", "will", "would", "should", "have", "has", "had", "not",
                     "in", "on", "at", "for", "to", "of", "with", "from", "after", "before", "then", "now", "today"}  # fmt: skip


def names_in(text: str) -> set[str]:
    """Capitalised words that look like names of people or places (common sentence starters excluded)."""
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text):
        if w[0].isupper() and len(w) > 1:
            low = w.lower().replace("'", "")
            low = low[:-1] if w.endswith("'s") else low
            if low not in SENTENCE_STARTERS:
                out.add(low)
    return out


def is_key(tok: str) -> bool:
    if (
        tok.startswith("#")
        or tok in NEGATIONS
        or tok in WEEKDAYS
        or tok in MONTHS
        or tok in DIRECTIONS
        or tok in COMPARISONS
    ):
        return True
    st = _stem(tok)
    return st in CONTRAST_VERBS or (st + "e") in CONTRAST_VERBS


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Minimal edit alignment: ('equal'|'sub'|'del'|'ins', ref index, hyp index)."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    ops: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append(("equal" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    return ops[::-1]


def key_findings(ref: list[str], hyp: list[str], names: set[str] = frozenset()) -> list[dict]:
    """Changes at key words, in script order. A name counts only when replaced by another name of the script
    (a misspelt or misheard name alone is not a meaning change)."""
    out = []
    for op, i, j in align(ref, hyp):
        r = ref[i] if i is not None else None
        h = hyp[j] if j is not None else None
        if op == "equal":
            continue
        if op == "sub":
            if is_key(r) or is_key(h):
                out.append({"type": "key_changed", "expected": r, "got": h, "at": i})
            elif r in names and h in names:
                out.append({"type": "name_swapped", "expected": r, "got": h, "at": i})
        elif op == "del" and is_key(r):
            out.append({"type": "key_missing", "expected": r, "at": i})
        elif op == "ins" and is_key(h):
            out.append({"type": "key_added", "got": h, "at": i})
    return out


def compare(expected: str, got: str) -> dict:
    ref, hyp = normalize_tokens(expected), normalize_tokens(got)
    ops = align(ref, hyp)
    edits = sum(1 for op, _, _ in ops if op != "equal")
    return {"ref": ref, "hyp": hyp, "ops": ops, "edits": edits, "findings": key_findings(ref, hyp, names_in(expected))}

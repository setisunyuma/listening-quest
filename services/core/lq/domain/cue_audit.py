"""Free checks for answer cues that work without listening (統合指示書 Q01 / V15). Heuristics, not proof: a flagged
item may still be fine, and a clean item may still be solvable from common sense - a blind "no audio" review is the
separate, paid check."""

from __future__ import annotations

import re
from collections import Counter

from lq.domain.content import UnitContent

HEDGE = re.compile(
    r"\b(check|confirm|ask|may|might|could|perhaps|possibly|not sure|unclear|cannot (be )?(decided|tell|say)|"
    r"can't tell|not yet|undecided|depends|some|partly|leaves? open|reluctant|only to)\b",
    re.I,
)
ABSOLUTE = re.compile(r"\b(all|always|never|every|any|none|nothing|completely|fully|firmly|sure|only)\b", re.I)
WORD = re.compile(r"[a-z']+")
STOP = set(
    "the a an of to in on at for and or is are was were be by with that this it its as from which who what how".split()
)


def _words(t: str) -> set[str]:
    return {w for w in WORD.findall(t.lower()) if w not in STOP and len(w) > 2}


def question_cues(prompt: str, choices: list[str], answer: int) -> list[str]:
    """Cues for one single-choice question (answer = 0-based index)."""
    cues = []
    ans, others = choices[answer], [c for i, c in enumerate(choices) if i != answer]
    if others and len(ans) > 1.25 * max(len(o) for o in others):
        cues.append("longest")  # only the answer is clearly longer
    if HEDGE.search(ans) and not any(HEDGE.search(o) for o in others):
        cues.append("hedge_only")  # the only cautious option is the answer
    if not ABSOLUTE.search(ans) and sum(1 for o in others if ABSOLUTE.search(o)) >= max(2, len(others) - 1):
        cues.append("absolutes_wrong")  # every wrong option is an absolute claim
    pw = _words(prompt)
    ov = [len(pw & _words(c)) for c in choices]
    if ov[answer] >= 2 and all(ov[answer] > v for i, v in enumerate(ov) if i != answer):
        cues.append("prompt_overlap")  # only the answer repeats the question's words
    return cues


def audit_units(units: list[UnitContent]) -> dict:
    flagged, positions, n = [], Counter(), 0
    for u in units:
        for q in u.sorted_questions():
            if q.type != "single_choice":
                continue
            ids = [c.id for c in q.choices]
            idx = ids.index(q.answer.choice_id)
            positions[idx + 1] += 1
            n += 1
            cues = question_cues(q.prompt_en, [c.text for c in q.choices], idx)
            if cues:
                flagged.append({"unit": u.content_id, "question": q.id, "cues": cues})
    return {"questions": n, "answer_positions": dict(sorted(positions.items())),
            "max_position_share": round(max(positions.values()) / n, 3) if n else 0.0, "flagged": flagged}  # fmt: skip

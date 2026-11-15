"""Practice track (統合実装指示書 A01 / M01): task definitions and objective scoring. Pure: no DB, no clock.

Four results are kept apart: listening content (per rubric item), task success (after permitted checks), English
expression (not scored here - optional, never part of the listening judgement) and the reliability of the input
(pending / ungradable). A critical misunderstanding (subject, negation, number, condition, attribution, certainty)
is reported on its own and is never offset by correct details or fluent wording.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

from lq.domain.content import UnitContent, is_correct

CRITICAL_JA = {
    "subject": "主体（誰が・何が）の取り違え", "conclusion": "結論の取り違え", "negation": "否定の取り違え", "number": "数値の取り違え",
    "condition": "条件の取り違え", "attribution": "話者・出典の取り違え", "certainty": "確実性（可能性と断定）の取り違え",
}  # fmt: skip


@lru_cache
def load_curriculum() -> dict:
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "curriculum.yaml").read_text(encoding="utf-8"))


def task_def(kind: str) -> dict:
    return load_curriculum()["tasks"][kind]


def lint_practice(unit: UnitContent) -> list[str]:
    """Structure checks for practice material against its task definition (free, before any paid step)."""
    p = unit.practice
    if p is None:
        return []
    errs: list[str] = []
    cur = load_curriculum()
    t = cur["tasks"].get(p.practice_kind)
    if t is None:
        return [f"unknown practice kind {p.practice_kind}"]
    if p.curriculum_version != cur["version"]:
        errs.append(f"curriculum version {p.curriculum_version} is not {cur['version']}")
    if unit.mode != t["stage"]:
        errs.append(f"{p.practice_kind} belongs to the {t['stage']} stage")
    rubric = {r["id"]: r for r in t["rubric"]}
    used = [it.rubric for it in p.items]
    for r in used:
        if r not in rubric:
            errs.append(f"rubric item {r!r} is not defined for {p.practice_kind}")
    if not any(rubric[r]["main"] for r in used if r in rubric):
        errs.append("a practice task needs at least one main rubric item")
    for it in p.items:
        r = rubric.get(it.rubric)
        if r and bool(r.get("main")) != it.main:
            errs.append(f"rubric item {it.rubric}: main must be {bool(r.get('main'))}")
        if r and r.get("stage", "final") != it.stage:
            errs.append(f"rubric item {it.rubric}: stage must be {r.get('stage', 'final')}")
        for kind in it.critical_choices.values():
            if kind not in t["critical"]:
                errs.append(f"rubric item {it.rubric}: critical kind {kind} is not part of {p.practice_kind}")
    allowed = int(t.get("clarifications", {}).get("max", 0))
    if p.max_clarifications > allowed:
        errs.append(f"{p.practice_kind} allows at most {allowed} clarification(s)")
    if t.get("prosody_required") and not p.prosody_required:
        errs.append(f"{p.practice_kind} depends on tone: prosody_required must be set")
    # Q01: strong cues that work without listening (only the answer is clearly longer / the only cautious option)
    from lq.domain.cue_audit import question_cues

    for q in unit.sorted_questions():
        if q.type == "single_choice":
            ids = [c.id for c in q.choices]
            cues = [
                c
                for c in question_cues(q.prompt_en, [c.text for c in q.choices], ids.index(q.answer.choice_id))
                if c in ("longest", "hedge_only")
            ]
            if cues:
                errs.append(
                    f"question {q.id}: the answer can be guessed without listening ({', '.join(cues)}); make the options alike"
                )
    return errs


_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower().strip()
    s = re.sub(r"[。、．，,.!?！？「」『』\"'()（）]", " ", s)
    return _WS.sub(" ", s).strip()


_NEG = re.compile(
    r"\b(not|no|never|isn't|aren't|wasn't|don't|doesn't|didn't|won't|can't|cannot)\b|ない|ません|なかった|不要|無理"
)


def _matches(accepted: str, text: str) -> bool:
    """The accepted content appears in the answer, and the answer adds no negation the accepted form lacks
    ("not Friday" never matches "Friday")."""
    if not accepted or accepted not in text:
        return False
    return bool(_NEG.search(accepted)) or not _NEG.search(text)


def _item_result(unit: UnitContent, it, ans: dict | None) -> dict:
    q = unit.question_map()[it.question_id]
    if ans is None:
        return {"correct": False, "skipped": True, "critical": None, "pending": False}
    if q.type == "single_choice":
        cid = ans.get("choice_id")
        ok = is_correct(q, ans) or cid in it.accept_choice_ids
        return {
            "correct": ok,
            "skipped": False,
            "critical": None if ok else it.critical_choices.get(cid),
            "pending": False,
        }
    if q.type == "blank":
        # the same content in Japanese, simple English or English with grammar slips is judged the same (V06)
        text = _norm(ans.get("text", ""))
        ok = bool(text) and any(_matches(_norm(a), text) for a in q.answer.accepted)
        pending = (not ok) and it.pending_if_unmatched and bool(text)
        return {"correct": ok, "skipped": False, "critical": None, "pending": pending}
    ok = is_correct(q, ans)
    return {"correct": ok, "skipped": False, "critical": None, "pending": False}


def score_practice(unit: UnitContent, final: dict, initial: dict | None, clarifications: list[str]) -> dict:
    """`final`: answers at submission; `initial`: answers before any permitted check (None = no check was used, so
    they are the same). First understanding comes from `initial` only; success after a check is recorded apart."""
    p = unit.practice
    assert p is not None
    initial = final if initial is None or not clarifications else initial
    items = {}
    for it in p.items:
        first = _item_result(unit, it, initial.get(it.question_id))
        after = _item_result(unit, it, final.get(it.question_id))
        items[it.question_id] = {
            "rubric": it.rubric,
            "main": it.main,
            "stage": it.stage,
            "first": first,
            "final": after,
        }

    def mean(key: str, main_only: bool = True) -> float | None:
        vals = [
            1.0 if v[key]["correct"] else 0.0
            for v in items.values()
            if (v["main"] or not main_only) and not v[key]["pending"]
        ]
        return round(sum(vals) / len(vals), 3) if vals else None

    crit_first = sorted({v["first"]["critical"] for v in items.values() if v["first"]["critical"]})
    crit_final = sorted({v["final"]["critical"] for v in items.values() if v["final"]["critical"]})
    pending = [k for k, v in items.items() if v["final"]["pending"] or v["first"]["pending"]]
    return {
        "practice_kind": p.practice_kind,
        "curriculum_version": p.curriculum_version,
        "items": items,
        "first_understanding": mean("first"),  # unaided first listening: main rubric items only
        "task_success": mean("final"),  # judgement / hand-over achieved (a permitted check may have helped)
        "clarifications": list(clarifications),
        "critical_first": crit_first,
        "critical_final": crit_final,
        "pending": pending,  # input that could not be judged: kept as 判定保留, never as wrong
        "expression": None,  # optional English expression is not scored as listening
    }


def evidence_eligible(result: dict, *, complete: bool, first_claim: bool, aids: dict, external_answer: bool,
                      invalid: bool, prosody_ok: bool) -> tuple[bool, list[str]]:  # fmt: skip
    """May this attempt count as objective evidence for unlocking (G01)? Reasons are kept for display."""
    why = []
    if not complete:
        why.append("未回答あり")
    if not first_claim:
        why.append("既出・同じ音声")
    if any(aids.get(k) for k in ("transcript", "hint", "prompt_text")):
        why.append("スクリプト・ヒントを使用")
    if external_answer:
        why.append("答えを先に見た")
    if invalid:
        why.append("教材が無効化")
    if result.get("pending"):
        why.append("判定保留の回答あり")
    if not prosody_ok:
        why.append("語調が実音声で未確認")
    return (not why), why

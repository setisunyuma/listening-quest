"""L01 (short repair loop), L02 (optional confidence) and L03 (phrase recall), joined to today's tasks (U01).

After an answer, the lines behind wrong answers, "guessed" correct answers (L02) and lines the learner marked
become repair items. One short repair task (about 30 s per line, at most 4 lines) is added to today's plan,
inside the day's time: if it does not fit, the last not-started problem moves on with a reason. Practice
ends with a check at normal speed without the text; that is "immediate" success only. The same line is then
checked again unaided on a later day (a "recall" task in that day's plan). Success on unseen audio with the
same skill ("transfer") is derived from later first attempts. The three are reported separately and never
change first-attempt statistics, the difficulty band or XP. No paid API is used."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from lq.db import models as m
from lq.services import learning as L
from lq.services import plans as P
from lq.services.content import load_content

SEC_PER_LINE = 30
MAX_LINES_PER_TASK = 4
MAX_REPAIR_TASKS_PER_DAY = 2
RECALL_SEC_PER_ITEM = 20
MAX_RECALL_ITEMS = 6
CONFIDENCE = ("sure", "unsure", "guess")
STEPS_JA = {
    "sound": "原音 → 短い区切りで聞く → 必要なときだけ英文 → もう一度原音",
    "vocabulary": "語句を文脈と音で確かめる → 音だけから意味を思い出す",
    "numbers_negation": "数字・否定・比較の箇所と、取り違えた内容との違いを確かめる",
    "inference": "根拠の箇所と、結論までのつながりを確かめる",
    "organizing": "根拠の箇所と、情報の対応関係を確かめる",
    "guess": "勘で当たった箇所の根拠を音で確かめる",
}


def _existing(session, user: m.User, unit_id: str, utt: str, kind: str, term: str | None) -> m.RepairItem | None:
    R = m.RepairItem
    q = select(R).where(R.user_id == user.id, R.unit_id == unit_id, R.utterance_id == utt, R.kind == kind)
    q = q.where(R.term.is_(None)) if term is None else q.where(R.term == term)
    return session.execute(q).scalar_one_or_none()


def items_from_attempt(session, user: m.User, att: m.Attempt) -> list[m.RepairItem]:
    """Repair items for one graded attempt: evidence lines of wrong answers and of correct-but-guessed answers,
    plus lines the learner marked as missed. Correct, confident answers add nothing (no blanket drills)."""
    unit = session.get(m.Unit, att.unit_id)
    content = load_content(unit)
    conf = (att.client_meta or {}).get("confidence") or {}
    picked: list[tuple[str, str, str | None]] = []  # (utterance, cause, skill)
    for q in content.sorted_questions():
        r = att.result["questions"][q.id]
        guessed = r["correct"] and conf.get(q.id) == "guess"
        if r["correct"] and not guessed:
            continue
        cause = "guess" if guessed else ((att.error_reasons or {}).get(q.id) or "unknown")
        for e in q.evidence[:2]:
            if e.utterance_id:
                picked.append((e.utterance_id, cause, q.skill_tags[0] if q.skill_tags else None))
    for ml in session.execute(
        select(m.MissedLine).where(m.MissedLine.user_id == user.id, m.MissedLine.unit_id == unit.id)
    ).scalars():
        picked.append((ml.utterance_id, "sound", unit.primary_skill))
    out, seen = [], set()
    for utt, cause, skill in picked:
        if utt in seen or len(out) >= MAX_LINES_PER_TASK:
            continue
        seen.add(utt)
        it = _existing(session, user, unit.id, utt, "line", None)
        if it is None:
            it = m.RepairItem(user_id=user.id, unit_id=unit.id, utterance_id=utt, kind="line", cause=cause, skill=skill,
                              source_attempt_id=att.id, stage="repair", results=[])  # fmt: skip
            session.add(it)
        elif it.stage == "done":
            it.stage, it.cause, it.source_attempt_id = "repair", cause, att.id  # missed again: practise again
        if it.stage == "repair":
            out.append(it)
    session.flush()
    return out


def add_repair_task(session, user: m.User, att: m.Attempt, now: datetime) -> m.PlanSlot | None:
    """Put one short repair task into today's active plan (U01), within the day's time. Returns None when there
    is nothing to repair, no plan today, or today's repair limit is reached (the items stay for a later day)."""
    day = L.user_day(user, now)
    plan = P.get_plan(session, user, day)
    if plan is not None:
        for s in P._plan_slots(session, plan.id):
            if s.kind == "repair" and s.payload.get("source_attempt_id") == att.id:
                return s if s.state in ("pending", "started") else None  # asked again (e.g. after a reload)
    items = items_from_attempt(session, user, att)
    if not items:
        return None

    def later():  # not placed today: the lines are not dropped, they come back as tomorrow's unaided check
        for it in items:
            it.stage, it.due_day = "delayed", day + timedelta(days=1)
        return None

    if plan is None or plan.status != "active" or plan.kind != "quest" or (plan.params or {}).get("ended_by_user"):
        return later()
    slots = P._plan_slots(session, plan.id, lock=True)
    reps = [s for s in slots if s.kind == "repair"]
    if len(reps) >= MAX_REPAIR_TASKS_PER_DAY:
        return later()
    used = sum(s.est_sec for s in P._live(slots))
    # make room inside the day's time: not-started, not-reserved problems move to a later day from the end
    # (never a started one); if that is not enough, fewer lines are practised now (the rest stay for later)
    for s in reversed(slots):
        if used + SEC_PER_LINE * len(items) <= plan.budget_sec:
            break
        if s.kind == "unit" and s.state == "pending" and not s.reserved:
            s.state, s.carry_reason = "carried", "修復を優先"
            used -= s.est_sec
    room = (plan.budget_sec - used) // SEC_PER_LINE
    if room < 1:
        return later()
    for it in items[int(room) :]:
        it.stage, it.due_day = "delayed", day + timedelta(days=1)
    items = items[: int(room)]
    est = SEC_PER_LINE * len(items)
    cause = items[0].cause
    slot = m.PlanSlot(plan_id=plan.id, idx=max(s.idx for s in slots) + 1, category="weak", mode=plan.mode, unit_id=att.unit_id,
                      time_cap_sec=est, est_sec=est, kind="repair", context="any",
                      payload={"item_ids": [i.id for i in items], "source_attempt_id": att.id, "cause": cause},
                      reason_code="repair", reason_text=f"直前の問題の修復（{len(items)}文・{STEPS_JA.get(cause, '原音で確かめる')}）")  # fmt: skip
    session.add(slot)
    plan.total_slots += 1
    session.flush()
    P._sync_planned(plan, P._plan_slots(session, plan.id))
    return slot


def _today(user: m.User, now: datetime) -> date:
    return L.user_day(user, now)


def task_view(session, user: m.User, slot_id: str) -> dict:
    slot, plan = P._own_slot(session, user, slot_id, lock=False)
    if slot.kind not in ("repair", "recall"):
        raise L.Invalid("not a repair or recall task")
    items = [session.get(m.RepairItem, i) for i in slot.payload.get("item_ids", [])]
    out = []
    for it in items:
        if it is None:
            continue
        unit = session.get(m.Unit, it.unit_id)
        content = load_content(unit)
        line = content.utterance_map().get(it.utterance_id)
        a = L.unit_assets(session, unit.id).get(f"utt:{it.utterance_id}")
        ja = {t.utterance_id: t.ja for t in content.study_notes.translations}.get(it.utterance_id)
        gloss = [g.model_dump() for g in content.study_notes.glossary if line and g.term.lower() in line.text.lower()]
        out.append({"item_id": it.id, "kind": it.kind, "term": it.term, "cause": it.cause, "steps_ja": STEPS_JA.get(it.cause, "原音で確かめる"),
                    "text": line.text if line else "", "ja": ja, "glossary": gloss, "audio_url": L.audio_url(a) if a else None,
                    "title": unit.title, "stage": it.stage})  # fmt: skip
    return {"slot_id": slot.id, "kind": slot.kind, "state": slot.state, "reason_text": slot.reason_text, "items": out}


def complete_task(session, user: m.User, slot_id: str, results: list[dict], now: datetime) -> dict:
    """Record a repair or recall task as done (it is done after the planned steps, success or not - no endless
    loop). repair: each line's final check (normal speed, no text) -> 'immediate'; the line is due again
    unaided on a later day. recall: unaided success finishes the line; aided or not yet -> another day."""
    slot, plan = P._own_slot(session, user, slot_id)
    if slot.kind not in ("repair", "recall"):
        raise L.Invalid("not a repair or recall task")
    if slot.state == "completed":
        return {"slot_id": slot.id, "already": True, **P._check_plan_done(session, user, plan, now)}
    by = {r.get("item_id"): r for r in results}
    today = _today(user, now)
    summary = {"ok": 0, "not_yet": 0, "aided": 0}
    for iid in slot.payload.get("item_ids", []):
        it = session.get(m.RepairItem, iid)
        if it is None or it.user_id != user.id:
            continue
        r = by.get(iid, {})
        ok, aided = bool(r.get("ok")), bool(r.get("aided"))
        step = "immediate" if slot.kind == "repair" else "delayed"
        it.results = [*(it.results or []), {"at": now.isoformat(), "step": step, "ok": ok, "aided": aided}]
        if slot.kind == "repair":
            it.stage, it.due_day = "delayed", today + timedelta(days=1)
        elif ok and not aided:
            it.stage, it.due_day = "done", None
        else:
            it.stage, it.due_day = "delayed", today + timedelta(days=1)  # not yet: once more later, no endless loop
        summary["ok" if ok and not aided else ("aided" if ok else "not_yet")] += 1
    slot.state, slot.completed_at, slot.result = "completed", now, {**summary, "results": results}
    return {"slot_id": slot.id, **summary, **P._check_plan_done(session, user, plan, now)}


def recall_slot_for_plan(session, user: m.User, plan: m.DailyPlan, today: date, budget_left: int) -> m.PlanSlot | None:
    """Lines and phrases due for the unaided later check become one recall task in today's plan (U01),
    within a fifth of the day's time."""
    R = m.RepairItem
    due = session.execute(
        # Q01: lines of invalidated material are not offered (the item and its history are kept)
        select(R).join(m.Unit, m.Unit.id == R.unit_id)
        .where(R.user_id == user.id, R.stage == "delayed", R.due_day <= today, m.Unit.invalidated_at.is_(None))
        .order_by(R.due_day, R.created_at).limit(MAX_RECALL_ITEMS)
    ).scalars().all()  # fmt: skip
    cap = min(budget_left, plan.budget_sec // 5)
    items = due[: max(0, cap // RECALL_SEC_PER_ITEM)]
    if not items:
        return None
    est = RECALL_SEC_PER_ITEM * len(items)
    return m.PlanSlot(plan_id=plan.id, idx=0, category="review", mode=plan.mode, unit_id=items[0].unit_id, time_cap_sec=est, est_sec=est,
                      kind="recall", context="any", payload={"item_ids": [i.id for i in items]}, reason_code="recall",
                      reason_text=f"後日の確認（{len(items)}件・英文なしで聞いて意味を思い出す）")  # fmt: skip


def choose_phrase(session, user: m.User, unit_id: str, utterance_id: str, term: str, on: bool, now: datetime) -> dict:
    """L03: only phrases the learner chooses are scheduled (from tomorrow); no automatic cards."""
    unit = L.get_unit_for(session, user, unit_id)
    content = load_content(unit)
    if utterance_id not in content.utterance_map():
        raise L.Invalid("unknown line")
    term = term.strip()[:80]
    if not term:
        raise L.Invalid("empty phrase")
    it = _existing(session, user, unit.id, utterance_id, "phrase", term)
    if on and it is None:
        it = m.RepairItem(user_id=user.id, unit_id=unit.id, utterance_id=utterance_id, kind="phrase", term=term, cause="vocabulary",
                          skill=None, stage="delayed", due_day=_today(user, now) + timedelta(days=1), results=[])  # fmt: skip
        session.add(it)
    elif not on and it is not None:
        it.stage, it.due_day = "paused", None  # paused, history kept
    elif on and it is not None and it.stage == "paused":
        it.stage, it.due_day = "delayed", _today(user, now) + timedelta(days=1)
    session.flush()
    return {"term": term, "on": on}


def outcomes(session, user: m.User, mode: str | None = None) -> dict:
    """The three outcomes, kept apart (L01): immediate (right after practice), delayed (same line, a later day,
    unaided), transfer (a later first, unaided attempt on DIFFERENT audio and family with the same skill was
    fully correct)."""
    R = m.RepairItem
    items = session.execute(select(R).where(R.user_id == user.id, R.kind == "line")).scalars().all()
    imm = sum(1 for i in items if any(r["step"] == "immediate" and r["ok"] for r in i.results or []))
    dly = sum(1 for i in items if any(r["step"] == "delayed" and r["ok"] and not r["aided"] for r in i.results or []))
    transfer, checked = 0, 0
    for i in items:
        if not i.skill:
            continue
        src = session.get(m.Unit, i.unit_id)
        if mode and src.mode != mode:
            continue
        checked += 1
        A, U = m.Attempt, m.Unit
        hit = session.execute(
            select(A.id).join(U, U.id == A.unit_id).where(
                A.user_id == user.id, A.strict_first.is_(True), A.status == "submitted", A.submitted_at > i.created_at,
                U.family_id != src.family_id, U.stimulus_fingerprint != src.stimulus_fingerprint, U.primary_skill == i.skill,
                A.unit_score == 1.0)
        ).first()  # fmt: skip
        transfer += 1 if hit else 0
    return {"lines": len(items), "immediate_ok": imm, "delayed_ok": dly, "transfer_ok": transfer, "transfer_checked": checked,
            "phrases": session.execute(select(R.id).where(R.user_id == user.id, R.kind == "phrase", R.stage != "paused")).all().__len__(),
            "note": "その場でできた・後日も同じ文でできた・別の初見音声でできた、を別々に数えます。初見の成績や難度帯には入りません。"}  # fmt: skip


__all__ = [
    "CONFIDENCE",
    "add_repair_task",
    "choose_phrase",
    "complete_task",
    "items_from_attempt",
    "outcomes",
    "recall_slot_for_plan",
    "task_view",
]

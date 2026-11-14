"""Main goal, goal transition and the state shown to the learner (統合実装指示書 P01).

- The main goal is the learner's choice. Nothing here switches it automatically (no exam date, XP, perfect score,
  band 5 or unlocked task does). A proposal is stored apart from the learner's decision.
- Goals are kept apart from per-mode history: a change of goal never deletes, resets or postpones a review.
- Targets without a date (Eiken / CEFR) are normal: no date is never shown as "overdue" or "unplanned".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lq.db import models as m
from lq.domain import progression as P
from lq.domain.content import MODES
from lq.services import learning as L

MODE_JA = {
    "common_test": "共通テスト",
    "eiken_pre1": "英検準1級",
    "eiken_1": "英検1級",
    "cefr_c1": "CEFR C1",
    "cefr_c2": "CEFR C2",
}
DECISIONS = ("adopt", "defer", "keep", "manual_switch", "return", "trial")


def _goals(session, user: m.User) -> dict[str, m.LearningGoal]:
    return {
        g.mode: g for g in session.execute(select(m.LearningGoal).where(m.LearningGoal.user_id == user.id)).scalars()
    }


def ensure_goals(session, user: m.User, now: datetime | None = None) -> dict[str, m.LearningGoal]:
    """Legacy users get their current mode as main goal (nothing else is guessed from old history)."""
    goals = _goals(session, user)
    cur = L.user_settings(user)["active_mode"]
    if not any(g.role == "current" for g in goals.values()):
        now = now or datetime.now(UTC)
        g = goals.get(cur)
        if g is None:
            g = m.LearningGoal(user_id=user.id, mode=cur, role="current", since=now, updated_at=now)
            session.add(g)
            goals[cur] = g
        else:
            g.role, g.updated_at = "current", now
        session.flush()
    return goals


def transition_state(session, user: m.User, today: date) -> dict:
    """The transition period: the first learning days after the learner chose a new main goal."""
    cfg = P.load_progression()
    goals = ensure_goals(session, user)
    cur = next(g for g in goals.values() if g.role == "current")
    if not cur.transition_started_day:
        return {"active": False, "mode": cur.mode}
    days = session.execute(
        select(func.count()).select_from(m.DailyPlan).where(
            m.DailyPlan.user_id == user.id, m.DailyPlan.kind == "quest",
            m.DailyPlan.learning_day >= cur.transition_started_day, m.DailyPlan.learning_day < today,
        )
    ).scalar_one()  # fmt: skip
    n = cfg["transition_period"]["learning_days"]
    return {"active": days < n, "mode": cur.mode, "from_mode": cur.transition_from, "day": days + 1, "of": n,
            "started": cur.transition_started_day.isoformat()}  # fmt: skip


# ---------------------------------------------------------------- evidence


def observations(session, user: m.User, modes: tuple[str, ...]) -> list[P.Obs]:
    """Observations for the pure rules, with their conditions kept: unaided first exposure (strict_first), later
    spaced checks without help, other attempts as 'assisted' / 'trial'. Invalidated material is left out."""
    out: list[P.Obs] = []
    SO, U = m.SkillObservation, m.Unit
    rows = session.execute(
        select(SO, U.part, U.family_id).join(U, U.id == SO.unit_id).where(
            SO.user_id == user.id, SO.mode.in_(modes), SO.kind == "strict_first", U.invalidated_at.is_(None))
    ).all()  # fmt: skip
    first_attempts = set()
    for so, part, fam in rows:
        out.append(
            P.Obs(
                day=so.created_at.date(),
                mode=so.mode,
                skill=so.skill,
                score=so.score,
                kind="first",
                scene=part,
                family=fam,
            )
        )
        first_attempts.add(so.attempt_id)
    A = m.Attempt
    atts = session.execute(
        select(A, U.primary_skill, U.part, U.family_id).join(U, U.id == A.unit_id).where(
            A.user_id == user.id, A.mode.in_(modes), A.status == "submitted", U.invalidated_at.is_(None),
            A.session_type.in_(("train", "review")))
    ).all()  # fmt: skip
    for a, skill, part, fam in atts:
        crit = bool((a.result or {}).get("critical_errors"))
        if L.is_unassisted_review(a) and a.review_was_due:
            out.append(
                P.Obs(
                    day=a.learning_day,
                    mode=a.mode,
                    skill=skill,
                    score=float(a.unit_score or 0),
                    kind="delayed",
                    scene=part,
                    family=fam,
                    critical=crit,
                )
            )
        elif a.id not in first_attempts and a.unit_score is not None:
            kind = "assisted" if (a.result or {}).get("assisted") else "trial"
            out.append(
                P.Obs(
                    day=a.learning_day,
                    mode=a.mode,
                    skill=skill,
                    score=float(a.unit_score),
                    kind=kind,
                    scene=part,
                    family=fam,
                    critical=crit,
                )
            )
        elif crit:
            out.append(
                P.Obs(
                    day=a.learning_day,
                    mode=a.mode,
                    skill=skill,
                    score=float(a.unit_score or 0),
                    kind="first",
                    scene=part,
                    family=fam,
                    critical=True,
                )
            )
    return out


def readiness(session, user: m.User, now: datetime) -> dict:
    """P01 proposal for the current goal, cached once per day and rules version (never overwrites decisions)."""
    cfg = P.load_progression()
    today = L.user_day(user, now)
    cur = L.user_settings(user)["active_mode"]
    nxt = P.next_stage(cur, cfg)
    modes = tuple(x for x in (cur, nxt) if x)
    res = P.transition_readiness(cur, observations(session, user, modes), today, cfg)
    with session.begin_nested():
        session.execute(
            pg_insert(m.ReadinessSnapshot).values(id=m.new_id(), user_id=user.id, mode=cur, computed_day=today, version=cfg["version"], result=res, created_at=now)
            .on_conflict_do_update(constraint="uq_readiness_snapshot", set_={"result": res, "created_at": now})
        )  # fmt: skip
    return res


# ---------------------------------------------------------------- decisions


def decide(
    session, user: m.User, event_id: str, decision: str, to_mode: str | None, now: datetime, *, replan: bool = True
) -> dict:
    """Record the learner's decision once (a re-sent event changes nothing) and apply it. Switching the main goal
    keeps every review, attempt, unlocked task and earlier goal; started tasks of today are not discarded."""
    if decision not in DECISIONS:
        raise L.Invalid("unknown decision")
    if to_mode is not None and to_mode not in MODES:
        raise L.Invalid("unknown mode")
    if session.get(m.GoalDecision, event_id) is not None:
        return overview(session, user, now)
    session.execute(select(m.User.id).where(m.User.id == user.id).with_for_update())
    cfg = P.load_progression()
    today = L.user_day(user, now)
    goals = ensure_goals(session, user, now)
    cur = next(g for g in goals.values() if g.role == "current")
    snap = None
    if decision in ("adopt", "defer", "keep"):
        snap = readiness(session, user, now)
        to_mode = to_mode or snap["next_mode"]
    if decision in ("adopt", "manual_switch", "return", "trial") and not to_mode:
        raise L.Invalid("to_mode is required")
    session.add(m.GoalDecision(id=event_id, user_id=user.id, decision=decision, from_mode=cur.mode, to_mode=to_mode,
                               readiness=snap or {}, version=cfg["version"], learning_day=today, created_at=now))  # fmt: skip
    if decision in ("adopt", "manual_switch", "return") and to_mode != cur.mode:
        cur.role, cur.updated_at = "maintained", now  # history and reviews stay; due reviews keep coming
        g = goals.get(to_mode)
        # going back to an earlier main goal by the mode switch is a return: no new transition period
        back = decision == "return" or (decision == "manual_switch" and g is not None and g.role == "maintained")
        if g is None:
            g = m.LearningGoal(user_id=user.id, mode=to_mode, role="current", since=now, updated_at=now)
            session.add(g)
        g.role, g.since, g.updated_at = "current", now, now
        if back:
            g.transition_from, g.transition_started_day = None, None
        else:
            g.transition_from, g.transition_started_day = cur.mode, today
        L.update_user_settings(user, {"active_mode": to_mode})
        if replan:
            _replan_unstarted(session, user, to_mode, now)
    elif decision == "trial" and to_mode not in (cur.mode,):
        g = goals.get(to_mode)
        if g is None:
            session.add(m.LearningGoal(user_id=user.id, mode=to_mode, role="trial", since=now, updated_at=now))
        elif g.role != "current":
            g.role, g.updated_at = "trial", now
    session.flush()
    return overview(session, user, now)


def _replan_unstarted(session, user: m.User, mode: str, now: datetime) -> None:
    from lq.services import plans

    plan = plans.get_plan(session, user, L.user_day(user, now))
    if plan is None or plan.kind != "quest" or plan.status != "active":
        return
    try:
        plans.rebuild_remaining(session, user, plan.id, mode, now)
    except L.Conflict:
        pass  # offline-reserved tasks stay as they are; the learner can rebuild after syncing


# ---------------------------------------------------------------- what the learner sees


def overview(session, user: m.User, now: datetime) -> dict:
    cfg = P.load_progression()
    today = L.user_day(user, now)
    goals = ensure_goals(session, user, now)
    cur = next(g for g in goals.values() if g.role == "current")
    res = readiness(session, user, now)
    last = session.execute(
        select(m.GoalDecision).where(m.GoalDecision.user_id == user.id, m.GoalDecision.from_mode == cur.mode,
                                     m.GoalDecision.decision.in_(("defer", "keep"))).order_by(m.GoalDecision.created_at.desc()).limit(1)
    ).scalar_one_or_none()  # fmt: skip
    deferred_until = None
    if last is not None:
        until = last.learning_day + timedelta(days=cfg["transition"]["reevaluate_days"])
        if today < until:
            deferred_until = until.isoformat()
    state = "recommended" if res["ready"] and not deferred_until else ("deferred" if res["ready"] else "not_yet")
    R, U = m.ReviewSchedule, m.Unit
    due = session.execute(
        select(U.mode, func.count()).select_from(R).join(U, U.id == R.unit_id)
        .where(R.user_id == user.id, R.kind == "recall", R.due_day <= today + timedelta(days=6), U.invalidated_at.is_(None))
        .group_by(U.mode)
    ).all()  # fmt: skip
    due_week = dict(due)
    exam_dates = L.user_settings(user).get("exam_dates") or {}
    rows = []
    for mode in MODES:
        g = goals.get(mode)
        target = exam_dates.get(mode)
        rows.append({
            "mode": mode, "label": MODE_JA[mode], "role": g.role if g else None, "target_date": target,
            # no date is a normal state for Eiken / CEFR: never "overdue" or "unplanned"
            "date_note_ja": None if target else "期日の設定は任意です",
            "due_reviews_week": int(due_week.get(mode, 0)),
        })  # fmt: skip
    decisions = session.execute(
        select(m.GoalDecision).where(m.GoalDecision.user_id == user.id).order_by(m.GoalDecision.created_at.desc()).limit(10)
    ).scalars().all()  # fmt: skip
    return {
        "current": {"mode": cur.mode, "label": MODE_JA[cur.mode], "since": cur.since.isoformat()},
        "transition": transition_state(session, user, today),
        "next_mode": res["next_mode"], "next_label": MODE_JA.get(res["next_mode"] or "", None),
        "proposal": {"state": state, "deferred_until": deferred_until, "readiness": res,
                     "adds_ja": "次の段階の新しい練習が日課の中心になります" if res["next_mode"] else None,
                     "keeps_ja": "これまでの段階の期限が来た復習は、日課の中で続きます（予定は消えません）"},
        "goals": rows,
        "decisions": [{"id": d.id, "decision": d.decision, "from_mode": d.from_mode, "to_mode": d.to_mode,
                       "day": d.learning_day.isoformat(), "version": d.version} for d in decisions],
        "note_ja": "移行の提案は次の級の合格や CEFR の認定ではありません。アプリ内の暫定的な目安に基づきます。",
        "version": cfg["version"],
    }  # fmt: skip

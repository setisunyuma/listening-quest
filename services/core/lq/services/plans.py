"""Daily plan: fixed at start (plan id, learning day, slot count, time caps), adapted only by swapping
the unit of not-yet-started, not-reserved slots. One shared time budget across the five modes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from lq.db import models as m
from lq.domain import recommend as rec
from lq.domain import rules
from lq.domain.content import MODES, learner_view
from lq.domain.profiles import current_profile, part_spec
from lq.services import learning as L
from lq.services.content import load_content


def candidate_units(session, user: m.User, mode: str, session_type: str = "train") -> list[m.Unit]:
    U = m.Unit
    rows = (
        session.execute(
            select(U)
            .where(
                U.realm == user.realm,
                U.mode == mode,
                U.status == "READY",
                U.invalidated_at.is_(None),
                U.superseded_by.is_(None),
                U.pool == "practice",
            )
            .order_by(U.id)
        )
        .scalars()
        .all()
    )
    return [u for u in rows if session_type in (u.session_types or [])]


def learner_context(
    session, user: m.User, mode: str, today: date, now: datetime | None = None, session_type: str = "train"
) -> tuple[list[rec.Candidate], rec.LearnerState, dict[str, m.Unit]]:
    """Everything the pure recommender needs. Only this mode's history is used: bands and skill stats
    are never copied between modes."""
    now = now or datetime.now(UTC)
    cfg = rules.load_config()
    units = candidate_units(session, user, mode, session_type)
    E = m.ExposureEvent
    touched_fp = set(session.execute(select(E.stimulus_fingerprint).where(E.user_id == user.id).distinct()).scalars())
    touched_units = set(session.execute(select(E.unit_id).where(E.user_id == user.id).distinct()).scalars())
    touched_family = set(session.execute(select(E.family_id).where(E.user_id == user.id).distinct()).scalars())
    lineage_touched = set()
    if touched_units:
        U = m.Unit
        lineage_touched = {
            (s, c) for s, c in session.execute(select(U.source, U.content_id).where(U.id.in_(touched_units))).all()
        }
    R = m.ReviewSchedule
    scheds = {
        r.unit_id: r for r in session.execute(select(R).where(R.user_id == user.id, R.kind == "recall")).scalars()
    }
    cands = []
    for u in units:
        touched = u.stimulus_fingerprint in touched_fp or (u.source, u.content_id) in lineage_touched
        sc = scheds.get(u.id)
        cands.append(rec.Candidate(
            unit_id=u.id, mode=u.mode, part=u.part, template_type=u.template_type, skills=tuple(u.skill_tags), primary_skill=u.primary_skill, topic=u.topic,
            band=u.difficulty_band, est_sec=u.estimated_duration_sec, family_id=u.family_id, fingerprint=u.stimulus_fingerprint, touched=touched,
            family_touched=(not touched) and u.family_id in touched_family, due_day=sc.due_day if sc else None, stage=sc.stage if sc else None,
            last_result="incorrect" if sc and sc.last_result == "incorrect" else (sc.last_result if sc else None),
        ))  # fmt: skip
    # recent attempts in this mode: need signals (cleared by a later unaided fully-correct answer on the skill)
    A = m.Attempt
    recent = session.execute(
        select(A).join(m.Unit, m.Unit.id == A.unit_id)
        .where(A.user_id == user.id, A.mode == mode, A.status == "submitted", A.session_type.in_(("train", "review")), A.submitted_at >= now - timedelta(days=7), m.Unit.invalidated_at.is_(None))
        .order_by(A.submitted_at)
    ).scalars().all()  # fmt: skip
    need: list[str] = []
    for a in recent:
        res = a.result or {}
        wrong = list(res.get("skills_wrong", []))
        if res.get("assisted"):
            unit = session.get(m.Unit, a.unit_id)
            wrong.append(unit.primary_skill)
        if not res.get("assisted") and res.get("complete"):
            need = [s for s in need if s not in res.get("skills_right", [])]
        for s in wrong:
            need = [x for x in need if x != s] + [s]
    need_skills = tuple(reversed(need))[:3]
    SO = m.SkillObservation
    stats: dict[str, tuple[int, float | None]] = {}
    obs = session.execute(
        select(SO.skill, SO.score)
        .join(m.Unit, m.Unit.id == SO.unit_id)
        .where(SO.user_id == user.id, SO.mode == mode, SO.kind == "strict_first", m.Unit.invalidated_at.is_(None))
        .order_by(SO.created_at.desc())
    ).all()
    by: dict[str, list[float]] = {}
    for skill, score in obs:
        if len(by.setdefault(skill, [])) < cfg.skill_window:
            by[skill].append(score)
    for skill, vals in by.items():
        stats[skill] = (len(vals), sum(vals) / len(vals))
    last = session.execute(
        select(m.Unit.template_type, m.Unit.topic, m.Unit.primary_skill)
        .join(A, A.unit_id == m.Unit.id)
        .where(A.user_id == user.id, A.mode == mode)
        .order_by(A.started_at.desc())
        .limit(15)
    ).all()

    def uniq(vals):
        out = []
        for v in vals:
            if v not in out:
                out.append(v)
        return tuple(out)

    state = rec.LearnerState(
        mode=mode, band=L.get_mode_state(session, user.id, mode).difficulty_band, today=today, need_skills=need_skills, skill_stats=stats,
        recent_templates=uniq(r[0] for r in last), recent_topics=uniq(r[1] for r in last), recent_skills=uniq(r[2] for r in last),
    )  # fmt: skip
    return cands, state, {u.id: u for u in units}


def _seed(user: m.User, day: date, mode: str, salt: str = "") -> str:
    return f"{user.id}:{day.isoformat()}:{mode}:{salt}"


def unit_card(unit: m.Unit) -> dict:
    try:
        part = part_spec(current_profile(unit.mode), unit.part)
    except KeyError:
        part = None
    return {"id": unit.id, "title": unit.title, "mode": unit.mode, "part": unit.part, "part_label": part["label_ja"] if part else unit.part, "template_type": unit.template_type,
            "band": unit.difficulty_band, "question_count": unit.question_count, "est_sec": unit.estimated_duration_sec, "audio_kind": unit.audio_kind, "primary_skill": unit.primary_skill}  # fmt: skip


def plan_dto(session, plan: m.DailyPlan) -> dict:
    slots = (
        session.execute(
            select(m.PlanSlot)
            .where(m.PlanSlot.plan_id == plan.id, m.PlanSlot.state != "removed")
            .order_by(m.PlanSlot.idx)
        )
        .scalars()
        .all()
    )
    out_slots = []
    for s in slots:
        unit = session.get(m.Unit, s.unit_id) if s.unit_id else None
        att = session.get(m.Attempt, s.attempt_id) if s.attempt_id else None
        out_slots.append({
            "id": s.id, "idx": s.idx, "category": s.category, "mode": s.mode, "state": s.state, "reserved": s.reserved, "assignment_version": s.assignment_version,
            "est_sec": s.est_sec, "time_cap_sec": s.time_cap_sec, "reason_text": s.reason_text, "reason_code": s.reason_code,
            "unit": unit_card(unit) if unit else None, "unit_revision": unit.revision if unit else None,
            "attempt_id": s.attempt_id, "attempt_status": att.status if att else None, "needs_all_answers": bool(att and att.status == "submitted" and not att.complete),
            "awaiting_feedback_ack": bool(att and att.status == "submitted" and att.complete and s.state != "completed"),
        })  # fmt: skip
    done = sum(1 for s in slots if s.state == "completed")
    return {
        "id": plan.id, "learning_day": plan.learning_day.isoformat(), "kind": plan.kind, "mode": plan.mode, "status": plan.status, "version": plan.version,
        "goal_minutes": plan.goal_minutes, "budget_sec": plan.budget_sec, "planned_sec": plan.planned_sec, "total_slots": plan.total_slots, "completed_slots": done,
        "remaining_sec": sum(s.est_sec for s in slots if s.state != "completed"), "notes": plan.params.get("notes", []), "radio_program_id": plan.radio_program_id, "slots": out_slots,
    }  # fmt: skip


def get_plan(session, user: m.User, day: date) -> m.DailyPlan | None:
    return session.execute(
        select(m.DailyPlan).where(m.DailyPlan.user_id == user.id, m.DailyPlan.learning_day == day)
    ).scalar_one_or_none()


def carried_plan(session, user: m.User, today: date) -> m.DailyPlan | None:
    """The most recent unfinished plan from an earlier learning day (it stays booked on its own day)."""
    return session.execute(
        select(m.DailyPlan)
        .where(
            m.DailyPlan.user_id == user.id,
            m.DailyPlan.learning_day < today,
            m.DailyPlan.status == "active",
            m.DailyPlan.learning_day >= today - timedelta(days=3),
        )
        .order_by(m.DailyPlan.learning_day.desc())
        .limit(1)
    ).scalar_one_or_none()


def preview_plan(session, user: m.User, now: datetime, mode: str | None = None) -> dict:
    s = L.user_settings(user)
    mode = mode or s["active_mode"]
    day = L.user_day(user, now)
    cands, state, _ = learner_context(session, user, mode, day, now)
    draft = rec.build_plan(cands, state, int(s["daily_goal_minutes"]) * 60, rules.load_config(), _seed(user, day, mode))
    cats: dict[str, int] = {}
    for sl in draft.slots:
        cats[sl["category"]] = cats.get(sl["category"], 0) + 1
    return {
        "mode": mode,
        "learning_day": day.isoformat(),
        "goal_minutes": int(s["daily_goal_minutes"]),
        "slots": len(draft.slots),
        "planned_sec": draft.planned_sec,
        "by_category": cats,
        "notes": draft.notes,
        "ready_units": len(cands),
        "untouched_units": sum(1 for c in cands if not c.touched),
    }


def start_plan(session, user: m.User, now: datetime, kind: str = "quest", mode: str | None = None) -> m.DailyPlan:
    """Create today's plan once. Later arrivals of new material or setting changes never grow it."""
    session.execute(select(m.User.id).where(m.User.id == user.id).with_for_update())
    s = L.user_settings(user)
    mode = mode or s["active_mode"]
    if mode not in MODES:
        raise L.Invalid("unknown mode")
    day = L.user_day(user, now)
    existing = get_plan(session, user, day)
    if existing:
        return existing
    budget = int(s["daily_goal_minutes"]) * 60
    cfg = rules.load_config()
    if kind == "radio":
        from lq.services import radio

        return radio.start_radio_plan(session, user, now, mode, budget, day)
    cands, state, _ = learner_context(session, user, mode, day, now)
    draft = rec.build_plan(cands, state, budget, cfg, _seed(user, day, mode))
    if not draft.slots:
        raise L.Invalid("no READY material for this mode yet")
    plan = m.DailyPlan(user_id=user.id, learning_day=day, kind="quest", mode=mode, goal_minutes=int(s["daily_goal_minutes"]), budget_sec=budget,
                       total_slots=len(draft.slots), planned_sec=draft.planned_sec, params={"seed": _seed(user, day, mode), "split": cfg.plan_split, "notes": draft.notes})  # fmt: skip
    session.add(plan)
    session.flush()
    for i, sl in enumerate(draft.slots):
        session.add(m.PlanSlot(plan_id=plan.id, idx=i, category=sl["category"], mode=mode, unit_id=sl["unit_id"], time_cap_sec=sl["time_cap_sec"], est_sec=sl["est_sec"],
                               reason_code=sl["reason_code"], reason_text=sl["reason_text"], reason_params=sl["reason_params"]))  # fmt: skip
    session.flush()
    return plan


def _plan_slots(session, plan_id: str, lock: bool = False) -> list[m.PlanSlot]:
    q = select(m.PlanSlot).where(m.PlanSlot.plan_id == plan_id, m.PlanSlot.state != "removed").order_by(m.PlanSlot.idx)
    return list(session.execute(q.with_for_update() if lock else q).scalars())


def _live(slots) -> list[m.PlanSlot]:
    return [s for s in slots if s.state != "removed"]


def _budget_cap(plan: m.DailyPlan, slots, slot: m.PlanSlot) -> int:
    """R07: a re-pick for `slot` may use its own cap, but never more than what the day's budget leaves after
    every other slot - and never less than the slot's current length (so a re-pick cannot grow the total)."""
    others = sum(x.est_sec for x in _live(slots) if x.id != slot.id)
    return max(min(slot.time_cap_sec, plan.budget_sec - others), min(slot.est_sec, slot.time_cap_sec))


def _sync_planned(plan: m.DailyPlan, slots) -> None:
    """The plan's planned time is always the sum of its slots (R07: the display matches the slots)."""
    plan.planned_sec = sum(x.est_sec for x in _live(slots))


def _assign(slot: m.PlanSlot, pick: rec.Pick) -> None:
    if slot.unit_id != pick.unit_id:
        slot.assignment_version += 1
    slot.unit_id = pick.unit_id
    slot.category = pick.category
    slot.est_sec = pick.est_sec
    slot.reason_code, slot.reason_text, slot.reason_params = pick.reason_code, pick.reason_text, pick.params


def after_unit(session, user: m.User, slot_id: str, now: datetime) -> None:
    """Today's answers change what comes next: re-pick the unit of every pending, unreserved slot within
    the same mode and time cap. Slot count, caps and started/completed/reserved slots never change."""
    slot = session.get(m.PlanSlot, slot_id)
    plan = session.execute(select(m.DailyPlan).where(m.DailyPlan.id == slot.plan_id).with_for_update()).scalar_one()
    if plan.kind != "quest":
        return
    slots = _plan_slots(session, plan.id, lock=True)
    fixed = {s.unit_id for s in slots if s.unit_id and (s.state != "pending" or s.reserved)}
    cfg = rules.load_config()
    today = L.user_day(user, now)
    ctx: dict[str, tuple] = {}
    first_pending = True
    used = set(fixed)
    for s in slots:
        if s.state != "pending" or s.reserved:
            continue
        if s.mode not in ctx:
            ctx[s.mode] = learner_context(session, user, s.mode, today, now)
        cands, state, _ = ctx[s.mode]
        seed = f"{plan.params.get('seed', plan.id)}:v{plan.version}:{s.idx}"
        pick = None
        if first_pending and state.need_skills:
            # the skill just missed gets the very next slot, if an untouched unit for it fits the cap
            miss_state = rec.LearnerState(
                mode=state.mode,
                band=state.band,
                today=state.today,
                need_skills=state.need_skills[:1],
                skill_stats={},
                recent_templates=state.recent_templates,
                recent_topics=state.recent_topics,
                recent_skills=state.recent_skills,
            )
            pick = rec.pick_weak(cands, miss_state, used, _budget_cap(plan, slots, s), seed, cfg)
        if pick is None:
            pick = rec.pick_for(s.category, cands, state, used, _budget_cap(plan, slots, s), seed, cfg)
        first_pending = False
        if pick is None:
            if s.unit_id:
                used.add(s.unit_id)  # nothing better: keep the current assignment
            continue
        _assign(s, pick)
        used.add(pick.unit_id)
    _sync_planned(plan, slots)


def complete_slot(session, user: m.User, slot_id: str, attempt_id: str, now: datetime) -> dict:
    slot = session.execute(select(m.PlanSlot).where(m.PlanSlot.id == slot_id).with_for_update()).scalar_one()
    plan = session.execute(select(m.DailyPlan).where(m.DailyPlan.id == slot.plan_id).with_for_update()).scalar_one()
    out = {"slot_completed": False, "plan_completed": False, "daily_xp": 0}
    if slot.state != "completed" and slot.attempt_id == attempt_id:
        slot.state = "completed"
        slot.completed_at = now
        slot.reserved = False
        out["slot_completed"] = True
    out.update(_check_plan_done(session, user, plan, now))
    return out


def _check_plan_done(session, user: m.User, plan: m.DailyPlan, now: datetime) -> dict:
    session.flush()
    left = session.execute(
        select(func.count())
        .select_from(m.PlanSlot)
        .where(m.PlanSlot.plan_id == plan.id, m.PlanSlot.state.notin_(("completed", "removed")))
    ).scalar_one()
    if left == 0 and plan.total_slots > 0:
        if plan.status != "completed":
            plan.status = "completed"
            plan.completed_at = now
        # booked on the plan's own learning day, once - whenever the last answer happens to arrive
        got = L.award_xp(
            session,
            user.id,
            "daily_plan",
            None,
            plan.learning_day,
            rules.load_config().xp["daily_plan"],
            f"daily_plan:{user.id}:{plan.learning_day.isoformat()}",
        )
        return {"plan_completed": True, "daily_xp": got}
    return {"plan_completed": False, "daily_xp": 0}


def replace_slot(session, user: m.User, slot_id: str, now: datetime, reason: str = "skip") -> dict:
    """Offer another unit for the same slot (broken / invalidated material, or the learner moves on after
    a partial answer). Skipping is never counted as correct or as a completed review."""
    slot = session.execute(select(m.PlanSlot).where(m.PlanSlot.id == slot_id).with_for_update()).scalar_one_or_none()
    plan = session.get(m.DailyPlan, slot.plan_id) if slot else None
    if slot is None or plan is None or plan.user_id != user.id:
        raise L.NotFound("slot not found")
    if slot.state in ("completed", "removed"):
        raise L.Conflict("slot already finished")
    if slot.reserved:
        raise L.Conflict("slot is reserved for offline use; sync and release it first")
    if slot.attempt_id:
        att = session.get(m.Attempt, slot.attempt_id)
        if att and att.status == "open" and (att.plays or att.aids):
            raise L.Conflict("finish or submit the current unit first")
    slots = _plan_slots(session, plan.id)
    used = {s.unit_id for s in slots if s.unit_id}
    cands, state, _ = learner_context(session, user, slot.mode, L.user_day(user, now), now)
    pick = rec.pick_for(
        slot.category,
        cands,
        state,
        used,
        _budget_cap(plan, slots, slot),
        f"{plan.params.get('seed', plan.id)}:replace:{slot.idx}:{slot.assignment_version}",
        rules.load_config(),
    )
    if pick is None:
        return {"replaced": False, "reason": "no alternative fits this slot right now"}
    _assign(slot, pick)
    slot.state = "pending"
    slot.attempt_id = None
    _sync_planned(plan, slots)
    return {"replaced": True, "slot_id": slot.id, "unit_id": pick.unit_id, "reason_text": pick.reason_text}


def rebuild_remaining(session, user: m.User, plan_id: str, new_mode: str, now: datetime) -> m.DailyPlan:
    """Explicit "switch the rest to this mode": only not-started, not-reserved slots are rebuilt; finished
    work and the remaining time budget carry over. This is the only operation that may change the number
    of slots, and the new amount is returned for display."""
    if new_mode not in MODES:
        raise L.Invalid("unknown mode")
    plan = session.execute(select(m.DailyPlan).where(m.DailyPlan.id == plan_id).with_for_update()).scalar_one_or_none()
    if plan is None or plan.user_id != user.id:
        raise L.NotFound("plan not found")
    if plan.kind != "quest" or plan.status != "active":
        raise L.Conflict("this plan cannot be rebuilt")
    slots = _plan_slots(session, plan.id, lock=True)
    if any(s.state == "pending" and s.reserved for s in slots):
        raise L.Conflict("some slots are reserved for offline use; sync and release them first")
    kept = [s for s in slots if s.state != "pending"]
    spent = sum(s.est_sec for s in kept)
    remaining = max(0, plan.budget_sec - spent)
    for s in slots:
        if s.state == "pending":
            s.state = "removed"
    session.flush()
    today = L.user_day(user, now)
    cands, state, _ = learner_context(session, user, new_mode, today, now)
    cfg = rules.load_config()
    plan.version += 1
    draft = (
        rec.build_plan(
            cands,
            state,
            remaining,
            cfg,
            f"{plan.params.get('seed', plan.id)}:rebuild:{plan.version}",
            exclude={s.unit_id for s in kept if s.unit_id},
        )
        if remaining > 0
        else rec.PlanDraft([], 0, 0, [])
    )
    idx = max((s.idx for s in slots), default=-1) + 1
    for sl in draft.slots:
        session.add(m.PlanSlot(plan_id=plan.id, idx=idx, category=sl["category"], mode=new_mode, unit_id=sl["unit_id"], time_cap_sec=sl["time_cap_sec"], est_sec=sl["est_sec"],
                               reason_code=sl["reason_code"], reason_text=sl["reason_text"], reason_params=sl["reason_params"]))  # fmt: skip
        idx += 1
    plan.mode = new_mode
    plan.total_slots = len(kept) + len(draft.slots)
    plan.planned_sec = spent + draft.planned_sec
    plan.params = {
        **plan.params,
        "notes": draft.notes,
        "rebuilt": [
            *plan.params.get("rebuilt", []),
            {"at": now.isoformat(), "mode": new_mode, "remaining_sec": remaining, "slots": len(draft.slots)},
        ],
    }
    session.flush()
    _check_plan_done(session, user, plan, now)
    return plan


# ---------------------------------------------------------------- offline reservation


def reserve_slots(session, user: m.User, plan_id: str) -> list[dict]:
    """Hand pending slots to the device for offline answering. A reserved slot keeps its unit version:
    adaptation in another tab cannot swap it."""
    plan = session.execute(select(m.DailyPlan).where(m.DailyPlan.id == plan_id).with_for_update()).scalar_one_or_none()
    if plan is None or plan.user_id != user.id:
        raise L.NotFound("plan not found")
    out = []
    for s in _plan_slots(session, plan.id, lock=True):
        if s.state != "pending" or not s.unit_id:
            continue
        s.reserved = True
        unit = session.get(m.Unit, s.unit_id)
        assets = L.unit_assets(session, unit.id)
        view = learner_view(load_content(unit))  # no answers, feedback or scripts in the offline package
        for seg in view["segments"]:
            a = assets.get(f"stimulus:{seg['id']}")
            seg["audio"] = (
                {"url": L.audio_url(a), "duration_ms": a.duration_ms, "bytes": a.bytes, "sha256": a.sha256}
                if a
                else None
            )
        out.append({"plan_id": plan.id, "slot_id": s.id, "idx": s.idx, "unit_id": unit.id, "unit_revision": unit.revision, "assignment_version": s.assignment_version,
                    "reason_text": s.reason_text, "est_sec": s.est_sec, "card": unit_card(unit), "unit": view})  # fmt: skip
    return out


def release_slots(session, user: m.User, plan_id: str) -> int:
    """After the device synced its answers: free the remaining reservations and bump the assignment
    version so stale device copies are recognisable."""
    plan = session.execute(select(m.DailyPlan).where(m.DailyPlan.id == plan_id).with_for_update()).scalar_one_or_none()
    if plan is None or plan.user_id != user.id:
        raise L.NotFound("plan not found")
    n = 0
    for s in _plan_slots(session, plan.id, lock=True):
        if s.reserved and s.state == "pending":
            s.reserved = False
            s.assignment_version += 1
            n += 1
    return n


def sync_offline_answers(session, user: m.User, items: list[dict], now: datetime) -> list[dict]:
    """Grade answers recorded offline. Each item: event_id (= attempt id), unit_id, unit_revision, answers,
    plays, optional plan_id / slot_id / assignment_version, client_started_at. Server time decides the
    learning day; the device clock is stored for reference only."""
    results = []
    for it in items:
        eid = it["event_id"]
        try:
            with session.begin_nested():
                existing = session.get(m.Attempt, eid)
                if existing is not None and existing.status == "submitted":
                    att = L.submit_attempt(session, user, eid, it["answers"], now=now)
                    results.append(
                        {
                            "event_id": eid,
                            "status": "already_synced",
                            "correct_count": att.result["correct_count"],
                            "total": att.result["total"],
                        }
                    )
                    continue
                unit = L.get_unit_for(session, user, it["unit_id"])
                if unit.revision != it.get("unit_revision", unit.revision):
                    raise L.Conflict("unit revision changed")
                slot_id = it.get("slot_id")
                note = None
                if slot_id:
                    slot = session.get(m.PlanSlot, slot_id)
                    if (
                        slot is None
                        or slot.unit_id != unit.id
                        or slot.assignment_version != it.get("assignment_version")
                        or slot.state in ("completed", "removed")
                    ):
                        slot_id, note = None, "slot_changed_recorded_as_free_practice"
                att = L.start_attempt(
                    session, user, attempt_id=eid, unit_id=unit.id, slot_id=slot_id, now=now, offline=True
                )
                if att.status == "open":
                    for seg, n in (it.get("plays") or {}).items():
                        for i in range(int(n)):
                            L.record_play(
                                session,
                                user,
                                att.id,
                                event_id=f"{eid}-p-{seg}-{i}"[:64],
                                segment_id=seg,
                                rate=float((it.get("rates") or {}).get(seg, 1.0)),
                            )
                att = L.submit_attempt(
                    session,
                    user,
                    att.id,
                    it["answers"],
                    now=now,
                    client_meta={
                        "client_started_at": it.get("client_started_at"),
                        "client_answered_at": it.get("client_answered_at"),
                        "offline": True,
                    },
                )
                results.append(
                    {
                        "event_id": eid,
                        "status": "synced",
                        "attempt_id": att.id,
                        "correct_count": att.result["correct_count"],
                        "total": att.result["total"],
                        "note": note,
                    }
                )
        except (L.Conflict, L.Invalid, L.NotFound) as e:
            results.append({"event_id": eid, "status": "rejected", "error": str(e)})
    return results

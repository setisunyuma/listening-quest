"""Radio: listen-through programs (questions, silent answer time, answers, explanation) rendered as one
continuous file. A separate, authenticated path: creating a program records answer access for every
unit in it, and listening alone never counts as correct or learned."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from lq.audio.pcm import decode_to_pcm, duration_ms, encode_mp3, silence
from lq.db import models as m
from lq.db.base import new_id
from lq.domain import rules
from lq.domain.content import sha256_hex
from lq.services import learning as L
from lq.services import plans
from lq.storage import get_storage

GAP_BETWEEN_UNITS_MS = 1500


def select_radio_units(
    session, user: m.User, mode: str, target_sec: int, today: date, include_new: bool
) -> list[tuple[m.Unit, m.Asset, bool]]:
    """Already-heard units first (due reviews first); untouched units only if the learner allows it,
    because radio access removes them from strict first-attempt statistics."""
    units = plans.candidate_units(session, user, mode, "radio")
    cands, _state, _ = plans.learner_context(session, user, mode, today, session_type="radio")
    info = {c.unit_id: c for c in cands}
    A = m.Asset
    assets = (
        {
            a.unit_id: a
            for a in session.execute(select(A).where(A.unit_id.in_([u.id for u in units]), A.kind == "radio")).scalars()
        }
        if units
        else {}
    )

    from lq.services.feedback import units_with_missed

    missed = units_with_missed(session, user)  # lines the learner could not catch come back on the radio first

    def key(u: m.Unit):
        c = info[u.id]
        due = c.due_day is not None and c.due_day <= today
        return (0 if due else (1 if u.id in missed else (2 if c.touched else 3)), rules_tiebreak(user.id, today, u.id))

    out, total = [], 0
    for u in sorted(units, key=key):
        a = assets.get(u.id)
        c = info[u.id]
        if a is None or (not c.touched and not include_new):
            continue
        if out and total + a.duration_ms / 1000 > target_sec:
            continue
        out.append((u, a, c.touched))
        total += a.duration_ms / 1000
        if total >= target_sec * 0.9:
            break
    return out


def rules_tiebreak(user_id: str, day: date, unit_id: str) -> int:
    from lq.domain.recommend import tiebreak

    return tiebreak(f"{user_id}:{day.isoformat()}:radio", unit_id)


def build_program(
    session,
    user: m.User,
    mode: str,
    now: datetime,
    *,
    target_sec: int | None = None,
    unit_ids: list[str] | None = None,
    include_new: bool | None = None,
) -> m.RadioProgram:
    s = L.user_settings(user)
    today = L.user_day(user, now)
    include_new = s["radio_include_new"] if include_new is None else include_new
    if unit_ids:
        chosen = []
        for uid in unit_ids:
            u = L.get_unit_for(session, user, uid)
            a = session.execute(
                select(m.Asset).where(m.Asset.unit_id == u.id, m.Asset.kind == "radio")
            ).scalar_one_or_none()
            if (
                u.status != "READY"
                or u.invalidated_at is not None
                or "radio" not in u.session_types
                or a is None
                or u.pool != "practice"
            ):
                raise L.Invalid("unit is not available for radio")
            chosen.append((u, a, True))
    else:
        chosen = select_radio_units(
            session, user, mode, target_sec or int(s["daily_goal_minutes"]) * 60, today, include_new
        )
    if not chosen:
        raise L.Invalid("no radio material is ready for this mode")
    st = get_storage()
    parts, timeline, pos = [], [], 0
    for i, (u, a, _touched) in enumerate(chosen):
        if i:
            parts.append(silence(GAP_BETWEEN_UNITS_MS))
            pos += GAP_BETWEEN_UNITS_MS
        pcm = decode_to_pcm(st.get(a.storage_key))
        d = duration_ms(pcm)
        timeline.append(
            {"unit_id": u.id, "title": u.title, "start_ms": pos, "end_ms": pos + d, "audio_kind": u.audio_kind}
        )
        parts.append(pcm)
        pos += d
    data = encode_mp3(b"".join(parts))
    aid = new_id()
    key = f"programs/{user.id}/{aid}.mp3"
    st.put(key, data, "audio/mpeg")  # object first, rows after
    session.add(
        m.Asset(
            id=aid,
            unit_id=None,
            kind="program",
            role=f"program:{aid}",
            storage_key=key,
            sha256=sha256_hex(data),
            bytes=len(data),
            duration_ms=pos,
            quality_state="decoded_ok",
            meta={"owner": user.id},
        )
    )
    sess = m.LearningSession(user_id=user.id, session_type="radio", mode=mode, meta={"day": today.isoformat()})
    session.add(sess)
    session.flush()
    prog = m.RadioProgram(
        user_id=user.id,
        mode=mode,
        unit_ids=[u.id for u, _, _ in chosen],
        asset_id=aid,
        timeline=timeline,
        duration_ms=pos,
        learning_day=today,
        session_id=sess.id,
        played_ranges=[],
    )
    session.add(prog)
    for u, _a, _t in chosen:
        # the file contains answers and explanations: from now on this unit is "answer-accessible"
        L.add_exposure(session, user.id, u, "radio_access", session_id=sess.id, meta={"program": aid}, at=now)
    session.flush()
    return prog


def start_radio_plan(session, user: m.User, now: datetime, mode: str, budget_sec: int, day: date) -> m.DailyPlan:
    prog = build_program(session, user, mode, now, target_sec=budget_sec)
    s = L.user_settings(user)
    plan = m.DailyPlan(user_id=user.id, learning_day=day, kind="radio", mode=mode, goal_minutes=int(s["daily_goal_minutes"]), budget_sec=budget_sec, total_slots=len(prog.unit_ids),
                       planned_sec=prog.duration_ms // 1000, params={"notes": []}, radio_program_id=prog.id)  # fmt: skip
    session.add(plan)
    session.flush()
    for i, t in enumerate(prog.timeline):
        sec = (t["end_ms"] - t["start_ms"]) // 1000
        session.add(
            m.PlanSlot(
                plan_id=plan.id,
                idx=i,
                category="radio",
                mode=mode,
                unit_id=t["unit_id"],
                time_cap_sec=sec,
                est_sec=sec,
                reason_code="radio",
                reason_text="ラジオで通し聞き",
            )
        )
    session.flush()
    return plan


def program_dto(session, prog: m.RadioProgram) -> dict:
    asset = session.get(m.Asset, prog.asset_id)
    cfg = rules.load_config()
    units = []
    for t in prog.timeline:
        cov = rules.coverage_ratio(prog.played_ranges, t["start_ms"], t["end_ms"])
        units.append({**t, "coverage": round(cov, 3), "complete": cov >= cfg.radio_complete_ratio})
    return {"id": prog.id, "mode": prog.mode, "learning_day": prog.learning_day.isoformat(), "duration_ms": prog.duration_ms, "position_ms": prog.position_ms,
            "audio": {"url": L.audio_url(asset), "bytes": asset.bytes, "sha256": asset.sha256, "duration_ms": asset.duration_ms}, "units": units,
            "played_ms": sum(b - a for a, b in prog.played_ranges)}  # fmt: skip


def get_program(session, user: m.User, program_id: str, lock: bool = False) -> m.RadioProgram:
    q = select(m.RadioProgram).where(m.RadioProgram.id == program_id)
    prog = session.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if prog is None or prog.user_id != user.id:
        raise L.NotFound("program not found")
    return prog


def report_progress(
    session,
    user: m.User,
    program_id: str,
    *,
    event_id: str,
    ranges: list[list[int]],
    position_ms: int,
    now: datetime | None = None,
) -> dict:
    """Merge actually played ranges (union). A unit is complete at >= 90% real coverage; seeking to the
    end adds no coverage. Idempotent per event id."""
    now = now or datetime.now(UTC)
    clean = [[max(0, int(a)), int(b)] for a, b in ranges if int(b) > int(a)]
    done = L.claim_event(
        session, user.id, event_id, "radio_progress", {"program": program_id, "ranges": clean, "pos": int(position_ms)}
    )
    if done is not None:
        return done
    prog = get_program(session, user, program_id, lock=True)
    clean = [[a, min(b, prog.duration_ms)] for a, b in clean if a < prog.duration_ms]
    before = rules.merge_ranges(prog.played_ranges)
    prog.played_ranges = rules.merge_ranges([*before, *clean])
    prog.position_ms = max(0, min(int(position_ms), prog.duration_ms))
    flag_modified(prog, "played_ranges")
    cfg = rules.load_config()
    awarded = []
    plan = session.execute(
        select(m.DailyPlan).where(m.DailyPlan.radio_program_id == prog.id).with_for_update()
    ).scalar_one_or_none()
    for t in prog.timeline:
        was = rules.coverage_ratio(before, t["start_ms"], t["end_ms"])
        cov = rules.coverage_ratio(prog.played_ranges, t["start_ms"], t["end_ms"])
        if cov > was:
            unit = session.get(m.Unit, t["unit_id"])
            L.add_exposure(
                session,
                user.id,
                unit,
                "radio_play",
                session_id=prog.session_id,
                meta={"program": prog.id, "coverage": round(cov, 3)},
                at=now,
            )
        if cov >= cfg.radio_complete_ratio:
            got = L.award_xp(
                session,
                user.id,
                "radio_unit",
                t["unit_id"],
                prog.learning_day,
                cfg.xp["radio_unit"],
                f"radio_unit:{user.id}:{t['unit_id']}:{prog.learning_day.isoformat()}",
            )
            if got:
                awarded.append({"kind": "radio_unit", "amount": got, "unit_id": t["unit_id"]})
            if plan is not None:
                slot = (
                    session.execute(
                        select(m.PlanSlot)
                        .where(
                            m.PlanSlot.plan_id == plan.id,
                            m.PlanSlot.unit_id == t["unit_id"],
                            m.PlanSlot.state != "removed",
                        )
                        .with_for_update()
                    )
                    .scalars()
                    .first()
                )
                if slot is not None and slot.state != "completed":
                    slot.state, slot.completed_at = "completed", now
    if plan is not None:
        info = plans._check_plan_done(session, user, plan, now)
        if info["daily_xp"]:
            awarded.append({"kind": "daily_plan", "amount": info["daily_xp"]})
    xp = L.total_xp(session, user.id)
    resp = {
        "awarded": awarded,
        "total_xp": xp,
        "level": rules.level_for(xp, cfg),
        "program": program_dto(session, prog),
    }
    return L.finish_event(session, event_id, resp)


def self_report(
    session,
    user: m.User,
    *,
    event_id: str,
    unit_id: str,
    value: str,
    session_id: str | None,
    now: datetime | None = None,
) -> dict:
    """Optional "I think I got it right / wrong / not sure" after listening. Stored as self_reported and
    never mixed with graded results. A self-reported miss may create a review; a self-reported hit never
    advances a review stage."""
    if value not in ("correct", "incorrect", "unknown"):
        raise L.Invalid("value must be correct, incorrect or unknown")
    now = now or datetime.now(UTC)
    done = L.claim_event(session, user.id, event_id, "self_report", {"unit": unit_id, "value": value})
    if done is not None:
        return done
    unit = L.get_unit_for(session, user, unit_id)
    session.add(m.SelfReport(id=event_id, user_id=user.id, unit_id=unit.id, session_id=session_id, value=value))
    sched = None
    if value == "incorrect" and "train" in unit.session_types:
        R = m.ReviewSchedule
        existing = session.execute(
            select(R).where(R.user_id == user.id, R.unit_id == unit.id, R.kind == "recall")
        ).scalar_one_or_none()
        if existing is None:
            sched = L.apply_schedule(
                session, user, unit, "incorrect", L.user_day(user, now), attempt_id=None, source="self_report"
            )
    return L.finish_event(session, event_id, {"saved": True, "value": value, "review_scheduled": sched})


def schedule_relisten(session, user: m.User, unit_id: str, now: datetime | None = None) -> dict:
    """Optional re-listen reminder from radio use. A different kind of schedule from recall reviews."""
    now = now or datetime.now(UTC)
    unit = L.get_unit_for(session, user, unit_id)
    return L.apply_schedule(
        session, user, unit, "incorrect", L.user_day(user, now), attempt_id=None, kind="relisten", source="radio"
    )

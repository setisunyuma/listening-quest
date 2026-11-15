"""Owner request 1/2: with generation on, the learner only follows "today's tasks"; the kinds needed for the next unlock
are restocked (balanced, several scenes), offered in the day, and the next task opens from the learner's own results."""

from collections import Counter

import pytest
from helpers import T0, days, eid, make_content, ready_unit
from sqlalchemy import select
from test_practice import choose, unit

from lq.db import models as m
from lq.domain.content import UnitContent
from lq.generator import budget
from lq.jobs import worker
from lq.services import goals, plans, practice, replenish
from lq.services import learning as L


def _do_practice_slot(db, user, plan, now):
    slot = next((s for s in plans._plan_slots(db, plan.id) if s.kind == "practice" and s.state == "pending"), None)
    if slot is None:
        return None
    u = db.get(m.Unit, slot.unit_id)
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, slot_id=slot.id, now=now)
    for seg in UnitContent.model_validate(u.content).segments:
        L.record_play(db, user, att.id, event_id=eid(), segment_id=seg.id)
    att = L.submit_attempt(db, user, att.id, choose(u, {}), now=now)
    L.ack_feedback(db, user, att.id, now=now)
    db.commit()
    return u


def test_restock_offer_and_unlock_from_the_daily_plan(db, user, monkeypatch):
    monkeypatch.setattr(replenish, "plan_jobs", lambda *a, **k: [])  # exam stock is not what this test is about
    budget.set_system(db, "demo", {"auto_replenish": True})
    L.update_user_settings(user, {"check_interval_days": 0})
    goals.decide(db, user, eid(), "manual_switch", "eiken_1", T0)  # the learner works on Eiken 1 (P2A, P2B)
    for i in range(3):
        ready_unit(db, content=make_content(mode="eiken_1", part="part1", seed=f"pp{i}"))
    unit(db, "pr-p2a-sleep")  # a small starting stock: one task of each kind
    unit(db, "pr-p2b-venue")
    db.commit()
    done: list[m.Unit] = []
    opened_day = None
    for d in range(24):
        now = T0 + days(d)
        replenish.run_for_user(db, user, now)  # what the worker does every few minutes
        db.commit()
        worker.drain("w")
        plan = plans.start_plan(db, user, now)  # the learner opens "today's tasks"
        db.commit()
        u = _do_practice_slot(db, user, plan, now)
        if u is not None:
            done.append(u)
        st = practice._states(db, user).get("P3A")
        if opened_day is None and st is not None and st.criteria_met_at is not None:
            opened_day = d
    kinds = Counter(u.part for u in done)
    assert (
        kinds["P2A"] >= 6 and kinds["P2B"] >= 6 and abs(kinds["P2A"] - kinds["P2B"]) <= 2
    )  # neither goal kind starves
    assert sum(kinds[k] for k in ("P2A", "P2B")) > sum(
        v for k, v in kinds.items() if k not in ("P2A", "P2B")
    )  # goal first
    p2b_scenes = {UnitContent.model_validate(u.content).practice.scene for u in done if u.part == "P2B"}
    assert len(p2b_scenes) >= 2  # the restock rotates scenes
    gen = db.execute(select(m.GenerationJob).where(m.GenerationJob.kind == "generate")).scalars().all()
    assert {j.params.get("practice_kind") for j in gen} >= {"P2A", "P2B", "P3A"}
    st = practice._states(db, user)["P3A"]
    assert opened_day is not None and st.status == "unlocked" and st.unlock_reason == "recommended"
    assert (
        st.criteria["status"] == "recommend" and st.tried_at is None and st.goal_opened_at is None
    )  # separate records
    # after the unlock, the newly opened C1 meeting task is offered in "today's tasks" (from the restocked stock)
    assert any(u.part == "P3A" for u in done if done.index(u) > 0)
    row = next(r for r in practice.overview(db, user, T0 + days(24))["tasks"] if r["kind"] == "P3A")
    assert (
        row["records"]["criteria_met_at"]
        and not row["records"]["goal_opened_at"]
        and row["reason_ja"] == "前提の証拠がそろった"
    )


def test_goal_switch_to_c1_puts_meeting_and_lecture_tasks_in_the_day(db, user):
    unit(db, "pr-p3a-festival")
    unit(db, "pr-p3b-bees")
    goals.decide(db, user, eid(), "manual_switch", "cefr_c1", T0)
    L.update_user_settings(user, {"check_interval_days": 0})
    db.commit()
    plan = plans.start_plan(db, user, T0)
    ps = [s for s in plans._plan_slots(db, plan.id) if s.kind == "practice"]
    assert len(ps) == 1 and ps[0].reason_params["practice_kind"] in ("P3A", "P3B")
    states = practice._states(db, user)
    assert (
        states["P3A"].goal_opened_at is not None
        and states["P3A"].criteria_met_at is None
        and states["P3A"].unlock_reason is None
    )
    rows = {r["kind"]: r for r in practice.overview(db, user, T0)["tasks"]}
    assert rows["P3A"]["reason_ja"] == "現在の主目標の練習" and rows["P3A"]["records"]["goal_opened_at"]


def test_waiting_for_material_is_shown_when_the_budget_stops_generation(db, user, live_user, monkeypatch):
    monkeypatch.setattr(replenish, "plan_jobs", lambda *a, **k: [])
    from test_pipeline import paid

    from lq.generator import factory

    paid(
        db, 0.5, paid_enabled=True, auto_replenish=True, daily_budget_usd=0.01, per_job_cap_usd=5
    )  # the budget is spent
    goals.decide(db, live_user, eid(), "manual_switch", "eiken_1", T0)
    db.commit()
    assert replenish.run_for_user(db, live_user, T0) > 0
    db.commit()
    worker.drain("w")
    st = practice.material_status(db, live_user, "P2B")
    assert st["state"] == "budget" and "予算の上限" in st["label_ja"]
    ready_unit(db, realm="live", content=make_content(mode="eiken_1", part="part1", seed="bw"))
    plan = plans.start_plan(db, live_user, T0)
    assert any("教材待ち" in n and "予算" in n for n in plan.params["notes"])
    factory.set_provider_override("live", None)


def test_the_proposal_uses_practice_as_its_own_indicator(db, user):
    goals.decide(db, user, eid(), "manual_switch", "eiken_1", T0)
    db.commit()
    res = goals.readiness(db, user, T0)
    assert set(res["practice"]) == {"P2A", "P2B"}
    codes = {x["code"] for x in res["missing"]}
    assert {"practice_unobserved:P2A", "practice_unobserved:P2B"} <= codes
    assert "skills" in res and not set(res["skills"]) & set(res["practice"])  # kept apart from exam-format skills
    with pytest.raises(StopIteration):
        next(c for c in codes if c.startswith("practice_") and "exam" in c)

"""統合指示書 G01 / P01 / U02 (V17-V24): goal transition is the learner's choice; reviews survive goal changes."""

from datetime import date, timedelta

from helpers import T0, days, do_attempt, eid, make_content, ready_unit
from sqlalchemy import func, select

from lq.db import models as m
from lq.domain import progression as P
from lq.services import backup, goals, plans
from lq.services import learning as L

D0 = date(2026, 11, 2)


def _obs(mode, skill, score, day, kind="first", scene="part1", fam=None, critical=False):
    return P.Obs(
        day=day,
        mode=mode,
        skill=skill,
        score=score,
        kind=kind,
        scene=scene,
        family=fam or f"{skill}-{day}",
        critical=critical,
    )


def _ready_history(mode="eiken_pre1"):
    t = P.load_progression()["transition"]
    out = []
    for i, s in enumerate(t["main_skills"][mode]):
        for k in range(2):
            out.append(_obs(mode, s, 1.0, D0 + timedelta(days=i % 3 + k), scene=("part1", "part3")[k]))
    out.append(_obs(mode, "main_idea", 1.0, D0 + timedelta(days=4), kind="delayed"))
    nxt = P.next_stage(mode)
    out += [_obs(nxt, "main_idea", 0.5, D0 + timedelta(days=5), kind="trial", fam=f"n{j}") for j in range(2)]
    return out


# ---------------------------------------------------------------- pure functions (each decision on its own)


def test_v19_readiness_names_each_missing_kind_of_evidence():
    today = D0 + timedelta(days=6)
    assert P.transition_readiness("eiken_pre1", _ready_history(), today)["ready"] is True
    empty = P.transition_readiness("eiken_pre1", [], today)
    codes = {x["code"] for x in empty["missing"]}
    assert not empty["ready"] and {"days", "scenes", "delayed", "next_trials"} <= codes
    assert any(c.startswith("skill_unobserved:") for c in codes)  # no data = 未確認, not "weak"
    assert all(v["status"] == "unobserved" for v in empty["skills"].values())
    # assisted results, radio and self reports never stand in for unaided first exposure
    fake = [
        _obs("eiken_pre1", o.skill, 1.0, o.day, kind=k, scene=o.scene)
        for o in _ready_history()
        if o.kind == "first"
        for k in ("assisted", "radio", "self_report")
    ]
    assert any(
        x["code"].startswith("skill_unobserved") for x in P.transition_readiness("eiken_pre1", fake, today)["missing"]
    )
    # a recent critical misunderstanding blocks the proposal; good details do not offset it
    crit = [*_ready_history(), _obs("eiken_pre1", "numbers_negation", 0.0, today, critical=True)]
    r = P.transition_readiness("eiken_pre1", crit, today)
    assert not r["ready"] and any(x["code"] == "critical" for x in r["missing"])
    # the last stage has nothing to move to
    assert P.transition_readiness("cefr_c2", [], today)["ready"] is False


def test_g01_unlock_is_per_kind_and_counts_distinct_families():
    ev = [
        P.TaskEvidence(
            day=D0 + timedelta(days=i % 3), kind="P1A", family=f"f{i}", scene=("desk", "move")[i % 2], score=0.9
        )
        for i in range(6)
    ]
    assert P.practice_unlock("P2B", ["P1A"], ev)["status"] == "recommend"
    same = [P.TaskEvidence(day=e.day, kind=e.kind, family="one", scene=e.scene, score=e.score) for e in ev]
    assert P.practice_unlock("P2B", ["P1A"], same)["status"] == "not_yet"  # the same script never counts twice
    ineligible = [
        P.TaskEvidence(day=e.day, kind=e.kind, family=e.family, scene=e.scene, score=1.0, eligible=False) for e in ev
    ]
    assert P.practice_unlock("P2B", ["P1A"], ineligible)["status"] == "unknown"  # explanations / radio / self reports
    low_other = [*ev, *[P.TaskEvidence(day=D0, kind="P1B", family=f"g{i}", scene="desk", score=0.3) for i in range(3)]]
    res = P.practice_unlock("P3A", ["P1A", "P1B"], low_other)
    assert (
        any(x["code"] == "mean:P1B" for x in res["missing"]) and res["means"]["P1A"] >= 0.8
    )  # never averaged together
    crit = [
        *ev[:-1],
        P.TaskEvidence(day=D0 + timedelta(days=2), kind="P1A", family="f9", scene="desk", score=1.0, critical=True),
    ]
    assert any(x["code"] == "critical" for x in P.practice_unlock("P2B", ["P1A"], crit)["missing"])


def test_v23_split_and_review_selection_hold_what_does_not_fit():
    sp = P.plan_split(1800, True)
    assert (sp["new"], sp["review"], sp["bridge"]) == (1080, 450, 270) and not sp["short_day"]
    assert P.plan_split(300, True)["short_day"] is True
    due = [
        P.DueReview(
            unit_id=f"u{i}",
            mode="eiken_pre1",
            due_day=D0,
            est_sec=120,
            skill=("a", "a", "a", "b", "c")[i],
            family=f"f{i}",
        )
        for i in range(5)
    ]
    picked, held = P.select_inherited_reviews(due, 300, D0)
    assert [d.unit_id for d in picked] == ["u0", "u1"]
    assert len(held) == 3 and all(h["reason_ja"] for h in held)  # held with a reason, never deleted
    gone = [
        P.DueReview(
            unit_id="x",
            mode="eiken_1",
            due_day=D0,
            est_sec=60,
            skill="a",
            family="z",
            available=False,
            unavailable_reason="教材が無効化",
        )
    ]
    assert P.select_inherited_reviews(gone, 600, D0) == (
        [],
        [{"unit_id": "x", "mode": "eiken_1", "reason_ja": "教材が無効化"}],
    )


# ---------------------------------------------------------------- services


def test_v18_v20_goal_is_never_switched_automatically_and_decisions_are_stored(db, user):
    ov = goals.overview(db, user, T0)
    assert ov["current"]["mode"] == "common_test" and ov["proposal"]["state"] == "not_yet"
    # practising another stage, XP or a perfect score does not change the main goal
    u = ready_unit(db, content=make_content(mode="eiken_pre1", part="part1", seed="pre1"))
    do_attempt(db, user, u)
    assert L.user_settings(user)["active_mode"] == "common_test"
    # direct start at C1 (no lower stage required), then defer, then return; history is kept
    out = goals.decide(db, user, "ev-c1-switch", "manual_switch", "cefr_c1", T0)
    assert out["current"]["mode"] == "cefr_c1" and L.user_settings(user)["active_mode"] == "cefr_c1"
    again = goals.decide(db, user, "ev-c1-switch", "manual_switch", "cefr_c2", T0)  # re-sent event: recorded once
    assert again["current"]["mode"] == "cefr_c1"
    goals.decide(db, user, "ev-defer", "defer", None, T0)
    assert goals.overview(db, user, T0)["current"]["mode"] == "cefr_c1"
    goals.decide(db, user, "ev-back", "return", "common_test", T0)
    db.commit()
    rows = db.execute(select(m.GoalDecision).where(m.GoalDecision.user_id == user.id)).scalars().all()
    assert sorted(r.decision for r in rows) == ["defer", "manual_switch", "return"]
    roles = {g.mode: g.role for g in db.execute(select(m.LearningGoal)).scalars()}
    assert roles == {"common_test": "current", "cefr_c1": "maintained"}
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == 1  # nothing lost


def test_v17_targets_without_a_date_are_normal(db, user):
    goals.decide(db, user, eid(), "manual_switch", "eiken_1", T0)
    ov = goals.overview(db, user, T0)
    row = next(r for r in ov["goals"] if r["mode"] == "eiken_1")
    assert row["target_date"] is None and "任意" in row["date_note_ja"]
    assert "期限切れ" not in str(ov) and "未計画" not in str(ov)
    db.commit()
    ready_unit(db, content=make_content(mode="eiken_1", part="part1", seed="e1-a"))
    plan = plans.start_plan(db, user, T0)  # a plan forms without any date
    assert plan.total_slots >= 1


def _review_due(db, user, mode, part, seed, now):
    u = ready_unit(db, content=make_content(mode=mode, part=part, seed=seed))
    do_attempt(db, user, u, correct=False, now=now)  # wrong: due the next day
    return u


def test_v21_v22_reviews_of_earlier_stages_stay_in_the_plan_after_switching(db, user):
    old = _review_due(db, user, "eiken_pre1", "part1", "old-r", T0)
    sched_before = db.execute(select(m.ReviewSchedule).where(m.ReviewSchedule.unit_id == old.id)).scalar_one()
    stage, due = sched_before.stage, sched_before.due_day
    goals.decide(db, user, eid(), "manual_switch", "eiken_1", T0 + days(0.5))
    db.commit()
    s = db.execute(select(m.ReviewSchedule).where(m.ReviewSchedule.unit_id == old.id)).scalar_one()
    assert (s.stage, s.due_day) == (stage, due)  # not reset, not postponed by the goal change
    for i in range(4):
        ready_unit(db, content=make_content(mode="eiken_1", part="part1", seed=f"new-{i}"))
    plan = plans.start_plan(db, user, T0 + days(3))
    slots = db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id)).scalars().all()
    inherited = [x for x in slots if x.reason_code == "review_inherited"]
    assert [x.unit_id for x in inherited] == [old.id] and inherited[0].mode == "eiken_pre1"
    assert any(x.mode == "eiken_1" for x in slots)
    assert plan.planned_sec <= plan.budget_sec + max(x.est_sec for x in slots)
    # solving a new-stage unit does not complete the old review
    new = next(x for x in slots if x.mode == "eiken_1" and x.kind == "unit")
    do_attempt(db, user, db.get(m.Unit, new.unit_id), now=T0 + days(3))
    s2 = db.execute(select(m.ReviewSchedule).where(m.ReviewSchedule.unit_id == old.id)).scalar_one()
    assert (s2.stage, s2.due_day, s2.last_result) == (stage, due, "incorrect")
    # a goal change keeps pending inherited reviews when the rest of the day is rebuilt
    goals.decide(db, user, eid(), "manual_switch", "cefr_c1", T0 + days(3))
    db.commit()
    kept = (
        db.execute(
            select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id, m.PlanSlot.reason_code == "review_inherited")
        )
        .scalars()
        .all()
    )
    assert [k.state for k in kept] == ["pending"]


def test_v23_five_minute_day_does_not_cram_old_reviews(db, user):
    _review_due(db, user, "eiken_pre1", "part1", "old-5", T0)
    goals.decide(db, user, eid(), "manual_switch", "eiken_1", T0)
    L.update_user_settings(user, {"daily_goal_minutes": 5})
    db.commit()
    ready_unit(db, content=make_content(mode="eiken_1", part="part1", seed="five"))
    plan = plans.start_plan(db, user, T0 + days(2))
    slots = db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id)).scalars().all()
    assert not [x for x in slots if x.reason_code in ("review_inherited", "bridge")]
    assert plan.params["held_reviews"] and "短い日" in plan.params["held_reviews"][0]["reason_ja"]
    assert any("保留" in n for n in plan.params["notes"])


def test_v24_invalidated_old_material_is_held_not_counted_as_decline(db, user):
    old = _review_due(db, user, "eiken_pre1", "part1", "old-bad", T0)
    goals.decide(db, user, eid(), "manual_switch", "eiken_1", T0)
    old.invalidated_at = T0
    db.commit()
    ready_unit(db, content=make_content(mode="eiken_1", part="part1", seed="inv"))
    plan = plans.start_plan(db, user, T0 + days(2))
    bad = (
        db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id, m.PlanSlot.unit_id == old.id))
        .scalars()
        .all()
    )
    assert not bad, [(b.kind, b.reason_code, b.mode) for b in bad]
    held = plan.params["held_reviews"]
    assert held and "能力の低下ではありません" in held[0]["reason_ja"]
    assert db.execute(select(m.ReviewSchedule).where(m.ReviewSchedule.unit_id == old.id)).scalar_one()  # kept


def test_transition_period_adds_a_bridge_and_ends_after_ten_learning_days(db, user):
    for i in range(3):
        ready_unit(db, content=make_content(mode="eiken_pre1", part="part1", seed=f"b-old-{i}"))
        ready_unit(db, content=make_content(mode="eiken_1", part="part1", seed=f"b-new-{i}"))
    L.update_user_settings(user, {"active_mode": "eiken_pre1"})
    goals.decide(db, user, eid(), "adopt", "eiken_1", T0)
    db.commit()
    tr = goals.transition_state(db, user, L.user_day(user, T0))
    assert tr["active"] and tr["from_mode"] == "eiken_pre1" and tr["day"] == 1
    plan = plans.start_plan(db, user, T0)
    slots = db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id)).scalars().all()
    assert [x.mode for x in slots if x.reason_code == "bridge"] == ["eiken_pre1"]
    for k in range(1, 11):
        db.add(m.DailyPlan(user_id=user.id, learning_day=L.user_day(user, T0) + timedelta(days=k), kind="quest", mode="eiken_1",
                           goal_minutes=30, budget_sec=1800, total_slots=0, planned_sec=0))  # fmt: skip
    db.commit()
    assert goals.transition_state(db, user, L.user_day(user, T0) + timedelta(days=11))["active"] is False


def test_v14_export_restore_keeps_goals_and_reads_version_one_files(db, user):
    goals.decide(db, user, eid(), "manual_switch", "cefr_c1", T0)
    db.commit()
    body = backup.export_user(db, user)
    assert body["export_version"] == 2 and body["data"]["goal_decisions"]
    rep = backup.restore_user(db, user, body)
    assert rep["ok"] and rep["inserted"]["goal_decisions"] == 0  # re-applying changes nothing
    v1 = {k: v for k, v in body.items() if k != "checksum"}
    v1["export_version"] = 1
    v1["data"] = {k: v for k, v in body["data"].items() if k not in backup.OPTIONAL_V1}
    from lq.domain.content import canonical_json, sha256_hex

    v1["checksum"] = sha256_hex(canonical_json({k: v for k, v in v1.items() if k != "exported_at"}))
    assert backup.validate_export(v1) == []
    roles = [g.role for g in db.execute(select(m.LearningGoal).where(m.LearningGoal.user_id == user.id)).scalars()]
    assert roles.count("current") == 1


def test_api_goals_and_settings_switch_is_recorded(db, user):
    from test_api import login

    c = login()
    ov = c.get("/api/goals").json()
    assert ov["current"]["mode"] == "common_test" and ov["next_mode"] == "eiken_pre1"
    assert "認定ではありません" in ov["note_ja"]
    assert c.patch("/api/settings", json={"active_mode": "eiken_pre1"}).status_code == 200
    r = c.post("/api/goals/decision", json={"event_id": "evt-12345678", "decision": "keep"}).json()
    assert r["current"]["mode"] == "eiken_pre1"
    decs = [d["decision"] for d in r["decisions"]]
    assert decs[:2] == ["keep", "manual_switch"]


def test_switching_back_by_the_mode_buttons_starts_no_new_transition(db, user):
    goals.decide(db, user, eid(), "manual_switch", "cefr_c1", T0)
    assert goals.transition_state(db, user, L.user_day(user, T0))["active"] is True
    goals.decide(db, user, eid(), "manual_switch", "common_test", T0)
    assert goals.transition_state(db, user, L.user_day(user, T0))["active"] is False

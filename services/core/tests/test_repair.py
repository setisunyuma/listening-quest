"""L01 repair loop, L02 confidence, L03 phrase recall - inside today's tasks (U01)."""

from datetime import timedelta

from helpers import T0, answers, eid, ready_unit
from test_api import login

from lq.db import models as m
from lq.services import content as C
from lq.services import learning as L
from lq.services import plans, repair


def _setup(db, user, n=8):
    for i in range(n):
        ready_unit(db, seed=f"rep-{i}")
    db.commit()
    plan = plans.start_plan(db, user, T0)
    db.commit()
    return plan, plans._plan_slots(db, plan.id)


def _answer(db, user, unit, slot_id=None, correct=True, confidence=None, now=T0):
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=unit.id, slot_id=slot_id, now=now)
    db.commit()
    for seg in C.load_content(unit).segments:
        for _ in range(seg.plays):
            L.record_play(db, user, att.id, event_id=eid(), segment_id=seg.id)
    att = L.submit_attempt(
        db, user, att.id, answers(unit, correct), now=now + timedelta(minutes=1), confidence=confidence
    )
    db.commit()
    ack = L.ack_feedback(db, user, att.id, now=now + timedelta(minutes=2))
    db.commit()
    return att, ack


def test_l01_wrong_answer_adds_a_short_repair_then_an_unaided_check_another_day(db, user):
    plan, slots = _setup(db, user)
    unit = db.get(m.Unit, slots[0].unit_id)
    att, ack = _answer(db, user, unit, slots[0].id, correct=False)
    rid = ack["repair_slot_id"]
    assert rid
    rep = db.get(m.PlanSlot, rid)
    assert rep.kind == "repair" and 1 <= len(rep.payload["item_ids"]) <= repair.MAX_LINES_PER_TASK
    assert plans.next_task(db, plan).id == rid  # the repair comes right after the problem
    assert sum(s.est_sec for s in plans._live(plans._plan_slots(db, plan.id))) <= plan.budget_sec
    first_before = db.query(m.Attempt).filter(m.Attempt.strict_first.is_(True)).count()
    band_before = L.get_mode_state(db, user.id, unit.mode).difficulty_band
    xp_before = L.total_xp(db, user.id)
    view = repair.task_view(db, user, rid)
    assert view["items"][0]["text"] and view["items"][0]["audio_url"]
    ids = rep.payload["item_ids"]
    repair.complete_task(
        db, user, rid, [{"item_id": i, "ok": True, "aided": False} for i in ids], T0 + timedelta(minutes=5)
    )
    db.commit()
    items = [db.get(m.RepairItem, i) for i in ids]
    assert all(i.stage == "delayed" and i.due_day == L.user_day(user, T0) + timedelta(days=1) for i in items)
    # immediate success changes nothing in first-attempt statistics, band or XP
    assert db.query(m.Attempt).filter(m.Attempt.strict_first.is_(True)).count() == first_before
    assert (
        L.get_mode_state(db, user.id, unit.mode).difficulty_band == band_before and L.total_xp(db, user.id) == xp_before
    )
    # next day: the unaided check is a task of the plan, first, within a fifth of the time
    nxt = plans.start_plan(db, user, T0 + timedelta(days=1))
    db.commit()
    rc = [s for s in plans._plan_slots(db, nxt.id) if s.kind == "recall"]
    assert len(rc) == 1 and set(rc[0].payload["item_ids"]) == set(ids) and rc[0].est_sec <= nxt.budget_sec // 5
    # aided success does not finish the line; unaided does
    res = [{"item_id": ids[0], "ok": True, "aided": True}] + [
        {"item_id": i, "ok": True, "aided": False} for i in ids[1:]
    ]
    repair.complete_task(db, user, rc[0].id, res, T0 + timedelta(days=1, minutes=3))
    db.commit()
    assert db.get(m.RepairItem, ids[0]).stage == "delayed"
    assert all(db.get(m.RepairItem, i).stage == "done" for i in ids[1:])
    out = repair.outcomes(db, user)
    assert out["immediate_ok"] == len(ids) and out["delayed_ok"] == len(ids) - 1  # kept apart, never merged


def test_l01_correct_confident_answers_add_no_drill(db, user):
    plan, slots = _setup(db, user)
    _, ack = _answer(db, user, db.get(m.Unit, slots[0].unit_id), slots[0].id, correct=True)
    assert ack["repair_slot_id"] is None


def test_l02_a_guessed_correct_answer_becomes_a_candidate_but_stays_correct(db, user):
    plan, slots = _setup(db, user)
    unit = db.get(m.Unit, slots[0].unit_id)
    qids = [q.id for q in C.load_content(unit).sorted_questions()]
    att, ack = _answer(db, user, unit, slots[0].id, correct=True, confidence={q: "guess" for q in qids})
    assert att.result["correct_count"] == att.result["total"]  # objective result unchanged
    assert att.client_meta["confidence"] == {q: "guess" for q in qids}
    assert (
        ack["repair_slot_id"]
        and db.get(m.RepairItem, db.get(m.PlanSlot, ack["repair_slot_id"]).payload["item_ids"][0]).cause == "guess"
    )
    # confidence cannot be rewritten after the answers are shown
    L.submit_attempt(db, user, att.id, answers(unit, True), confidence={q: "sure" for q in qids})
    db.refresh(att)
    assert att.client_meta["confidence"] == {q: "guess" for q in qids}


def test_l03_only_chosen_phrases_are_scheduled_and_can_be_paused(db, user):
    plan, slots = _setup(db, user)
    unit = db.get(m.Unit, slots[0].unit_id)
    _answer(db, user, unit, slots[0].id, correct=True)
    line = C.load_content(unit).segments[0].utterances[0]
    term = line.text.split()[0]
    bad = login().post(f"/api/units/{unit.id}/phrases", json={"utterance_id": line.id, "term": "", "on": True})
    assert bad.status_code == 422
    repair.choose_phrase(db, user, unit.id, line.id, term, True, T0)
    db.commit()
    it = db.query(m.RepairItem).filter(m.RepairItem.kind == "phrase").one()
    assert it.stage == "delayed" and it.due_day == L.user_day(user, T0) + timedelta(days=1)
    nxt_day = plans.start_plan(db, user, T0 + timedelta(days=1))
    db.commit()
    rc = [s for s in plans._plan_slots(db, nxt_day.id) if s.kind == "recall"]
    assert rc and it.id in rc[0].payload["item_ids"]  # a chosen phrase reaches tomorrow's tasks
    repair.choose_phrase(db, user, unit.id, line.id, term, False, T0)
    db.commit()
    db.refresh(it)
    assert it.stage == "paused"
    later = plans.start_plan(db, user, T0 + timedelta(days=2))
    db.commit()
    assert not [s for s in plans._plan_slots(db, later.id) if s.kind == "recall" and it.id in s.payload["item_ids"]]


def test_repair_never_pushes_the_day_over_budget_and_is_limited_per_day(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 5})
    plan, slots = _setup(db, user)
    for i, s in enumerate(slots[:3]):
        if s.state != "pending":
            continue
        _answer(db, user, db.get(m.Unit, s.unit_id), s.id, correct=False, now=T0 + timedelta(minutes=10 * i))
    all_slots = plans._plan_slots(db, plan.id)
    assert sum(s.est_sec for s in plans._live(all_slots)) <= plan.budget_sec
    assert len([s for s in all_slots if s.kind == "repair"]) <= repair.MAX_REPAIR_TASKS_PER_DAY
    assert all(s.carry_reason == "修復を優先" for s in all_slots if s.state == "carried")


def test_lines_not_placed_today_come_back_tomorrow_and_nothing_is_added_after_the_day_ended(db, user):
    plan, slots = _setup(db, user)
    plans.end_day(db, user, plan.id, T0)
    db.commit()
    unit = db.get(m.Unit, slots[0].unit_id)
    _, ack = _answer(db, user, unit, None, correct=False)  # answered from the library after ending the day
    assert ack["repair_slot_id"] is None
    items = db.query(m.RepairItem).all()
    assert items and all(i.stage == "delayed" and i.due_day == L.user_day(user, T0) + timedelta(days=1) for i in items)
    nxt = plans.start_plan(db, user, T0 + timedelta(days=1))
    db.commit()
    rc = [s for s in plans._plan_slots(db, nxt.id) if s.kind == "recall"]
    assert rc and {i.id for i in items} <= set(rc[0].payload["item_ids"])

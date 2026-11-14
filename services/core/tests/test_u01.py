"""U01: today's tasks - finish states, shortening, skipping, carry-over, library dedupe, on the move."""

from datetime import timedelta

from helpers import T0, do_attempt, ready_unit

from lq.db import models as m
from lq.services import plans


def _units(db, n=8, **kw):
    out = [ready_unit(db, seed=f"u01-{kw.get('part', '3')}-{i}", **kw) for i in range(n)]
    db.commit()
    return out


def _plan(db, user, now=T0):
    p = plans.start_plan(db, user, now)
    db.commit()
    return p, plans._plan_slots(db, p.id)


def test_t05_shorten_and_end_carry_tasks_with_reasons_never_completed(db, user):
    _units(db)
    plan, slots = _plan(db, user)
    assert len(slots) >= 2
    r = plans.shorten(db, user, plan.id, 0, T0)  # "今日はあと0分"
    db.commit()
    assert r["carried"] == len(slots) and r["plan_ended"] and not r["plan_completed"]
    db.refresh(plan)
    assert plan.status == "ended"
    summary = plans.day_summary(db, plan)
    assert summary["label"] == "今日はここまで" and summary["carried"] == len(slots)
    assert all(x["reason"].startswith("時間短縮") for x in summary["moved"])
    assert not db.query(m.XpEvent).filter(m.XpEvent.kind == "daily_plan").count()  # no daily bonus


def test_skip_is_not_done_and_moves_to_the_next_day_once(db, user):
    _units(db)
    plan, slots = _plan(db, user)
    first = slots[0]
    plans.skip_task(db, user, first.id, T0)
    db.commit()
    assert first.state == "skipped"
    for s in slots[1:]:  # finish the rest properly
        att = do_attempt(db, user, db.get(m.Unit, s.unit_id), slot_id=s.id)
        assert att.status == "submitted"
    db.refresh(plan)
    assert plan.status == "ended"  # not "completed": one task was skipped
    tomorrow = T0 + timedelta(days=1)
    nxt, nslots = _plan(db, user, tomorrow)
    carried = [s for s in nslots if s.carried_from]
    assert [s.carried_from for s in carried] == [first.id] and carried[0].idx == 0
    assert carried[0].reason_text.startswith("前回からの持ち越し（飛ばした）")
    assert plans.start_plan(db, user, tomorrow).id == nxt.id  # same day again: no second plan, no duplicate carry
    # skipped again and carried a second time: counted, and only from the newest source
    plans.skip_task(db, user, carried[0].id, tomorrow)
    db.commit()
    third, tslots = _plan(db, user, tomorrow + timedelta(days=1))
    again = {s.carried_from: s for s in tslots if s.carried_from}
    assert first.id not in again  # the original is never carried twice
    assert carried[0].id in again and again[carried[0].id].payload["carry_count"] == 2


def test_day_change_carries_unfinished_tasks_and_ends_the_old_plan(db, user):
    _units(db)
    plan, slots = _plan(db, user)
    do_attempt(db, user, db.get(m.Unit, slots[0].unit_id), slot_id=slots[0].id)
    nxt, nslots = _plan(db, user, T0 + timedelta(days=1))
    db.refresh(plan)
    assert plan.status == "ended"
    assert all(s.state in ("completed", "carried") for s in plans._plan_slots(db, plan.id))
    assert {s.carried_from for s in nslots if s.carried_from} <= {s.id for s in slots[1:]}
    assert sum(s.est_sec for s in nslots if s.carried_from) <= nxt.budget_sec // 2  # new material keeps coming
    assert nxt.planned_sec == sum(s.est_sec for s in nslots)


def test_t10_library_attempt_completes_todays_task_once(db, user):
    _units(db)
    plan, slots = _plan(db, user)
    ids = [s.unit_id for s in slots]
    assert len(ids) == len(set(ids))  # a unit appears once in a day's plan
    unit = db.get(m.Unit, slots[0].unit_id)
    att = do_attempt(db, user, unit)  # from the library, no slot
    db.refresh(slots[0])
    assert slots[0].state == "completed" and slots[0].attempt_id == att.id
    att2 = do_attempt(db, user, unit, now=T0 + timedelta(minutes=30))
    assert att2.slot_id is None  # the task is done; a second solve is free practice


def test_on_the_move_waits_for_the_desk(db, user):
    figure_unit = ready_unit(db, seed="u01-desk", part="5")  # part 5 carries a figure
    _units(db, n=6)
    plan, slots = _plan(db, user)
    slots[0].unit_id = figure_unit.id
    for s in slots[1:]:
        s.state = "completed"
    db.commit()
    assert plans.task_context(db, slots[0]) == "desk"
    assert plans.next_task(db, plan, on_the_move=True) is None
    assert plans.next_task(db, plan).id == slots[0].id  # still in the plan for the desk, not marked done
    assert slots[0].state == "pending"

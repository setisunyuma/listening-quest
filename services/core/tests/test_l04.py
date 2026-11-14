"""L04: a short check on unseen material inside the day, with its conditions kept, as a reference value only."""

from helpers import T0, days, do_attempt, make_content, ready_unit
from sqlalchemy import select

from lq.db import models as m
from lq.services import learning as L
from lq.services import plans, radio, stats


def _slots(db, plan, cat):
    return [
        s for s in db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id)).scalars() if s.category == cat
    ]


def test_short_check_uses_unseen_material_records_conditions_and_repeats_after_the_interval(db, user):
    units = [ready_unit(db, content=make_content(part="1A", seed=f"chk{i}")) for i in range(8)]
    radio.build_program(db, user, "common_test", T0, unit_ids=[units[0].id])  # heard on the radio: never in a check
    db.commit()
    plan = plans.start_plan(db, user, T0)
    chk = _slots(db, plan, "check")
    assert len(chk) >= 2 and units[0].id not in {s.unit_id for s in chk}
    cond = plan.params["check"]["conditions"]
    assert (
        cond["presentation"] == "official"
        and cond["mode"] == "common_test"
        and set(cond["units"]) == {s.unit_id for s in chk}
    )
    assert plan.planned_sec <= plan.budget_sec + max(s.est_sec for s in chk)
    for s in chk:
        do_attempt(db, user, db.get(m.Unit, s.unit_id), slot_id=s.id, now=T0 + days(0.01))
    g = stats.growth(db, user, T0 + days(1), "common_test")["short_checks"]
    row = g["checks"][0]
    assert (
        row["reference_only"] and row["units_done"] == len(chk) and row["questions"] > 0 and "断定しません" in g["note"]
    )
    # within the interval no new check; the interval can be switched off
    assert not _slots(db, plans.start_plan(db, user, T0 + days(1)), "check")
    L.update_user_settings(user, {"check_interval_days": 0})
    db.commit()
    assert not _slots(db, plans.start_plan(db, user, T0 + days(8)), "check")


def test_short_check_is_skipped_with_a_note_when_unseen_stock_is_short(db, user):
    ready_unit(db, content=make_content(part="1A", seed="only"))
    plan = plans.start_plan(db, user, T0)
    assert not _slots(db, plan, "check") and "見送りました" in plan.params["check_skipped"]

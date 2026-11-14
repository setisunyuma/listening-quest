"""R11: a unit withdrawn after a radio program was built is skipped there and the program can be rebuilt."""

from datetime import timedelta

from helpers import T0, eid, make_content, ready_unit
from sqlalchemy import func, select

from lq.db import models as m
from lq.services import radio


def test_withdrawn_unit_in_an_existing_program_is_flagged_skipped_and_rebuilt(db, user):
    a = ready_unit(db, content=make_content(part="1A", seed="ra"))
    b = ready_unit(db, content=make_content(part="1A", seed="rb"))
    prog = radio.build_program(db, user, "common_test", T0, unit_ids=[a.id, b.id])
    db.commit()
    assert radio.program_dto(db, prog)["stale"] is False
    b.invalidated_at = T0 + timedelta(minutes=5)  # reported / withdrawn after the program was made
    db.commit()
    dto = radio.program_dto(db, prog)
    tb = next(t for t in prog.timeline if t["unit_id"] == b.id)
    assert dto["stale"] is True and dto["skip_ranges"] == [[tb["start_ms"], tb["end_ms"]]]
    assert [u["withdrawn"] for u in dto["units"]] == [False, True]
    # coverage over the withdrawn part earns nothing
    r = radio.report_progress(
        db,
        user,
        prog.id,
        event_id=eid(),
        ranges=[[tb["start_ms"], tb["end_ms"]]],
        position_ms=tb["end_ms"],
        now=T0 + timedelta(minutes=6),
    )
    assert not [x for x in r["awarded"] if x.get("unit_id") == b.id]
    new = radio.rebuild_program(db, user, prog.id, T0 + timedelta(minutes=7))
    db.commit()
    assert new.unit_ids == [a.id] and new.id != prog.id
    assert db.get(m.RadioProgram, prog.id) is not None  # the old program and its history stay
    assert (
        db.execute(
            select(func.count()).select_from(m.ExposureEvent).where(m.ExposureEvent.unit_id == b.id)
        ).scalar_one()
        >= 1
    )

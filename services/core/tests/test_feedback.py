"""Learner feedback: reports on a unit (withdraw / restore) and lines the learner could not catch."""

from helpers import do_attempt, ready_unit
from test_api import login

from lq.db import models as m
from lq.services import content as C
from lq.services import feedback, radio
from lq.services import learning as L


def test_wrong_answer_report_withdraws_the_unit_and_the_owner_can_restore_it(db, user):
    unit = ready_unit(db, seed="rep1")
    db.commit()
    att = do_attempt(db, user, unit)
    c = login()
    r = c.post(f"/api/units/{unit.id}/reports", json={"kind": "wrong", "note": "答えが2つある", "attempt_id": att.id})
    assert r.status_code == 200 and r.json()["withdrawn"] is True
    db.expire_all()
    assert db.get(m.Unit, unit.id).invalidated_at is not None  # out of plans, radio and statistics
    reps = c.get("/api/admin/reports").json()["reports"]
    assert [(x["kind"], x["note"], x["unit_withdrawn"]) for x in reps] == [("wrong", "答えが2つある", True)]
    r = c.post(f"/api/admin/reports/{reps[0]['id']}/resolve", json={"action": "restore"})
    assert r.status_code == 200 and r.json()["unit_withdrawn"] is False
    db.expire_all()
    assert db.get(m.Unit, unit.id).invalidated_at is None
    assert c.get("/api/admin/reports").json()["reports"] == []  # settled
    assert len(c.get("/api/admin/reports?all=true").json()["reports"]) == 1  # history kept


def test_unclear_report_is_only_recorded(db, user):
    unit = ready_unit(db, seed="rep2")
    db.commit()
    c = login()
    r = c.post(f"/api/units/{unit.id}/reports", json={"kind": "unclear", "note": "seed exchange が分からない"})
    assert r.status_code == 200 and r.json()["withdrawn"] is False
    db.expire_all()
    assert db.get(m.Unit, unit.id).invalidated_at is None
    assert c.post(f"/api/units/{unit.id}/reports", json={"kind": "nonsense"}).status_code == 422


def test_reports_need_login_and_csrf(db, user):
    unit = ready_unit(db, seed="rep3")
    db.commit()
    from fastapi.testclient import TestClient

    from lq.api.app import app

    assert TestClient(app).post(f"/api/units/{unit.id}/reports", json={"kind": "other"}).status_code in (401, 403)


def test_missed_lines_are_marked_listed_with_audio_and_cleared(db, user):
    unit = ready_unit(db, seed="miss1")
    db.commit()
    att = do_attempt(db, user, unit)
    uid = C.load_content(unit).segments[0].utterances[0].id
    c = login()
    assert c.post(f"/api/units/{unit.id}/missed", json={"utterance_id": uid, "missed": True}).json()["missed"] == [uid]
    # idempotent
    assert c.post(f"/api/units/{unit.id}/missed", json={"utterance_id": uid, "missed": True}).json()["missed"] == [uid]
    assert c.get(f"/api/attempts/{att.id}/result").json()["missed"] == [uid]
    lines = c.get("/api/missed").json()["lines"]
    assert len(lines) == 1 and lines[0]["utterance_id"] == uid and lines[0]["audio_url"].startswith("/api/audio/")
    assert c.post(f"/api/units/{unit.id}/missed", json={"utterance_id": "nope", "missed": True}).status_code == 422
    assert c.post(f"/api/units/{unit.id}/missed", json={"utterance_id": uid, "missed": False}).json()["missed"] == []
    assert c.get("/api/missed").json()["lines"] == []


def test_radio_plays_units_with_missed_lines_before_other_heard_units(db, user):
    units = [ready_unit(db, seed=f"rad{i}") for i in range(4)]
    db.commit()
    for u in units:
        do_attempt(db, user, u)
    target = units[-1]
    feedback.set_missed(db, user, target.id, C.load_content(target).segments[0].utterances[0].id, True)
    db.commit()
    from helpers import T0

    chosen = radio.select_radio_units(db, user, target.mode, 10_000, L.user_day(user, T0), include_new=False)
    assert chosen and chosen[0][0].id == target.id


def test_study_notes_do_not_change_the_content_hash_and_appear_only_with_results(db, user):
    from helpers import make_content

    from lq.domain.content import content_hash

    base = make_content(seed="notes1")
    plain = base.model_copy(update={"study_notes": type(base.study_notes)()})
    assert content_hash(base) == content_hash(plain)  # learning aids are not part of the graded meaning
    unit = ready_unit(db, content=base)
    db.commit()
    att = L.start_attempt(db, user, attempt_id="a" * 32, unit_id=unit.id, slot_id=None)
    db.commit()
    c = login()
    before = c.get(f"/api/attempts/{att.id}").text
    assert "デモ用の仮訳" not in before and "デモ用の語注" not in before  # nothing before answering
    after = do_attempt(db, user, unit)
    res = c.get(f"/api/attempts/{after.id}/result").json()
    assert res["glossary"] and res["glossary"][0]["meaning_ja"].startswith("（デモ用の語注）")
    assert all(line["ja"] for seg in res["transcript"] for line in seg["lines"])


def test_authored_notes_keep_only_real_lines():
    from lq.generator.authoring import study_notes

    out = study_notes(
        [{"term": "seed exchange", "meaning_ja": "種の交換"}, {"term": " ", "meaning_ja": ""}],
        [{"utterance_id": "u1", "ja": "訳"}, {"utterance_id": "ghost", "ja": "x"}, {"utterance_id": "u2", "ja": " "}],
        {"u1", "u2"},
    )
    assert out == {
        "glossary": [{"term": "seed exchange", "meaning_ja": "種の交換"}],
        "translations": [{"utterance_id": "u1", "ja": "訳"}],
    }


def test_growth_names_what_to_practise_next_and_counts_own_reasons(db, user):
    unit = ready_unit(db, seed="grow1")
    db.commit()
    att = do_attempt(db, user, unit, correct=False)
    q = C.load_content(unit).sorted_questions()[0]
    L.set_error_reason(db, user, att.id, q.id, "vocabulary")
    feedback.set_missed(db, user, unit.id, C.load_content(unit).segments[0].utterances[0].id, True)
    db.commit()
    g = login().get(f"/api/stats/growth?mode={unit.mode}").json()
    assert 1 <= len(g["next_focus"]) <= 3 and all(f["reason_ja"] for f in g["next_focus"])
    assert g["error_reasons"] == {"vocabulary": 1} and g["missed_lines"] == 1

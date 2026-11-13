"""Acceptance tests 1 (API half), 2, 15, 16, 18, 19 plus authentication and audio delivery."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import T0, answers, do_attempt, eid, make_content, ready_unit
from sqlalchemy import func, select

from lq.api.app import app
from lq.db import models as m
from lq.domain.profiles import current_profile, score_exam, slot_layout, total_slots
from lq.generator.demo import register_demo_oracle
from lq.jobs import queue, worker
from lq.services import assessment, backup, plans, stats
from lq.services import content as C
from lq.services import learning as L


def login(username="demo", password="demo-password-123") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def secrets_of(unit):
    content = C.load_content(unit)
    q = content.questions[0]
    out = [q.feedback.explanation_ja, q.feedback.distractors[0].reason_ja, content.segments[0].utterances[0].text, '"answer"', "evidence", "explanation_ja", "correct_answer"]
    if q.feedback.answer_en not in [c.text for c in q.choices]:  # the answer sentence may legitimately equal a printed choice
        out.append(q.feedback.answer_en)
    return out


# ---- auth


def test_auth_cookie_csrf_and_login_limit(db, user):
    anon = TestClient(app)
    assert anon.get("/api/home").status_code == 401
    assert anon.get("/api/library").status_code == 401
    r = anon.post("/api/auth/login", json={"username": "demo", "password": "demo-password-123"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "password" not in r.text
    assert anon.get("/api/home").status_code == 200
    assert anon.patch("/api/settings", json={"daily_goal_minutes": 20}).status_code == 403  # no CSRF token
    anon.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert anon.patch("/api/settings", json={"daily_goal_minutes": 20}).json()["daily_goal_minutes"] == 20
    assert anon.post("/api/auth/logout").status_code == 200 and anon.get("/api/home").status_code == 401
    bad = TestClient(app)
    codes = [bad.post("/api/auth/login", json={"username": "demo", "password": "wrong-password"}).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    assert bad.post("/api/auth/login", json={"username": "demo", "password": "demo-password-123"}).status_code == 429
    assert db.execute(select(m.User.password_hash)).scalar_one().startswith("$argon2")


def test_defaults(db, user):
    s = login().get("/api/home").json()["settings"]
    assert (s["active_mode"], s["daily_goal_minutes"], s["timezone"], s["day_cutoff_hour"], s["sfx"]) == ("common_test", 30, "Asia/Tokyo", 4, False)
    meta = login().get("/api/meta").json()
    assert [x["mode"] for x in meta["modes"]] == ["common_test", "eiken_pre1", "eiken_1", "cefr_c1", "cefr_c2"]


# ---- 1. + 2. generation -> library -> play -> answer -> grading -> next pick, with locks at every step


def test_full_flow_and_answer_locks(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 5})
    db.commit()
    c = login()
    assert c.get("/api/library").json()["count"] == 0 and c.get("/api/home").json()["preview"]["notes"] == ["no_ready_units"]
    for i, skill in enumerate(["numbers_negation", "numbers_negation", "explicit_detail", "inference", "main_idea", "speaker_attitude"]):
        assert c.post("/api/admin/jobs", json={"mode": "common_test", "part": "3", "primary_skill": skill, "band": 3, "count": 1}).status_code == 200, i
    assert {j["status"] for j in c.get("/api/admin/jobs").json()["jobs"]} == {"QUEUED"}
    assert set(worker.drain("w")) == {"succeeded"}
    lib = c.get("/api/library?mode=common_test").json()  # polling picks the new units up; no reload needed
    assert lib["count"] == 6 and {u["status_label"] for u in lib["units"]} == {"未学習"} and {u["audio_kind"] for u in lib["units"]} == {"demo_tone"}

    plan = c.post("/api/plans/today", json={"kind": "quest"}).json()
    slot = plan["slots"][0]
    unit = db.get(m.Unit, slot["unit"]["id"])
    aid = eid()
    r = c.post("/api/attempts", json={"attempt_id": aid, "unit_id": unit.id, "slot_id": slot["id"]})
    att = r.json()
    for secret in secrets_of(unit):  # learner DTO: no key, evidence, explanation or script
        assert secret not in r.text, secret
    assert att["counts_as_first"] and att["unit"]["questions"][0]["choices"]
    seg = att["unit"]["segments"][0]

    # stimulus audio: authenticated, Range capable
    assert TestClient(app).get(seg["audio"]["url"]).status_code == 401
    full = c.get(seg["audio"]["url"])
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes" and len(full.content) == seg["audio"]["bytes"]
    part = c.get(seg["audio"]["url"], headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == full.content[100:200] and part.headers["content-range"] == f"bytes 100-199/{len(full.content)}"
    assert c.get(seg["audio"]["url"], headers={"Range": "bytes=-50"}).content == full.content[-50:]
    assert c.get(seg["audio"]["url"], headers={"Range": f"bytes={len(full.content)}-"}).status_code == 416

    # before submission: nothing answer-bearing is reachable
    assets = {a.role: a for a in db.execute(select(m.Asset).where(m.Asset.unit_id == unit.id)).scalars()}
    assert c.get(f"/api/attempts/{aid}/result").status_code == 403
    for role in ("feedback", "radio", "exam", "utt:u1"):
        assert c.get(f"/api/audio/{assets[role].id}").status_code == 404, role
    assert c.post(f"/api/attempts/{aid}/ack").status_code == 409  # reward cannot precede the answer
    assert c.post(f"/api/attempts/{aid}/plays", json={"event_id": eid(), "segment_id": seg["id"], "rate": 1.0}).json()["plays"] == {seg["id"]: 1}

    wrong = answers(unit, correct=False)
    res = c.post(f"/api/attempts/{aid}/submit", json={"answers": wrong})
    body = res.json()
    assert res.status_code == 200 and body["correct_count"] == 0 and body["counted_as_first"] is True
    q = body["questions"][0]
    assert q["explanation_ja"] and q["evidence"][0]["text"] and q["distractors"] and q["correct_answer"]["choice_id"] != q["your_answer"]["choice_id"]
    assert q["system_guess_label"] and body["schedule"]["reason_ja"] == "初回学習の翌日に最初の復習"
    assert c.post(f"/api/attempts/{aid}/submit", json={"answers": wrong}).json() == body  # resend: identical result
    assert c.post(f"/api/attempts/{aid}/submit", json={"answers": answers(unit)}).status_code == 409
    assert c.get(body["feedback_audio"]["url"]).status_code == 200 and c.get(q["evidence"][0]["audio_url"]).status_code == 200  # unlocked now
    assert c.post(f"/api/attempts/{aid}/error-reasons", json={"question_id": q["id"], "reason": "numbers_negation"}).json()["self_reasons"] == {q["id"]: "numbers_negation"}
    ack = c.post(f"/api/attempts/{aid}/ack").json()
    assert ack["awarded"][0] == {"kind": "unit_base", "amount": 10} and ack["slot_completed"]
    assert c.post(f"/api/attempts/{aid}/ack").json()["awarded"] == []

    after = c.get(f"/api/plans/{plan['id']}").json()
    assert after["total_slots"] == plan["total_slots"] and after["completed_slots"] == 1
    missed = unit.primary_skill
    nxt = after["slots"][1]
    if any(u["primary_skill"] == missed and u["id"] != unit.id for u in lib["units"]):
        assert nxt["reason_code"] == "weak_skill" and nxt["unit"]["primary_skill"] == missed and "再確認" in nxt["reason_text"]
    home = c.get("/api/home").json()
    assert home["xp"]["total"] == 10 and home["streak_days"] == 1
    hist = c.get("/api/history").json()["attempts"][0]
    assert hist["counted_as_first"] and hist["self_reasons"] and hist["plays"] == {seg["id"]: 1}


def test_aid_is_recorded_before_text_is_returned_and_gives_no_answers(db, user):
    unit = ready_unit(db, part="2", skill="numbers_negation", seed="aid")  # question is presented by audio only
    c = login()
    aid = eid()
    att = c.post("/api/attempts", json={"attempt_id": aid, "unit_id": unit.id}).json()
    assert att["unit"]["questions"][0]["prompt_en"] is None and att["unit"]["questions"][0]["prompt_medium"] == "audio"
    r = c.post(f"/api/attempts/{aid}/aids", json={"event_id": eid(), "kind": "transcript"})
    assert r.json()["aids"] == {"transcript": True} and r.json()["segments"][0]["lines"][0]["text"]
    assert db.get(m.Attempt, aid).aids == {"transcript": True}
    assert db.execute(select(m.ExposureEvent.kind).where(m.ExposureEvent.attempt_id == aid)).scalars().all() == ["transcript"]
    content = C.load_content(unit)
    for secret in (content.questions[0].feedback.explanation_ja, content.questions[0].feedback.answer_en, "choice_id"):
        assert secret not in r.text
    p = c.post(f"/api/attempts/{aid}/aids", json={"event_id": eid(), "kind": "prompt_text"}).json()
    assert p["prompts"] and c.get(f"/api/attempts/{aid}").json()["unit"]["questions"][0]["prompt_en"]
    assert c.get(f"/api/attempts/{aid}/result").status_code == 403  # aids never unlock feedback
    body = c.post(f"/api/attempts/{aid}/submit", json={"answers": answers(unit)}).json()
    assert body["counted_as_first"] is False and {"transcript", "prompt_text"} <= set(body["not_first_reasons"]) and body["category"] == "assisted"


def test_radio_path_serves_explanations_and_records_access(db, user):
    u = ready_unit(db, seed="rad")
    c = login()
    radio_asset = db.execute(select(m.Asset).where(m.Asset.unit_id == u.id, m.Asset.kind == "radio")).scalar_one()
    assert c.get(f"/api/audio/{radio_asset.id}").status_code == 404  # unit radio track is never served directly
    prog = c.post("/api/radio/programs", json={"mode": "common_test", "minutes": 5}).json()
    assert c.get(prog["audio"]["url"]).status_code == 200 and prog["units"][0]["unit_id"] == u.id
    kinds = db.execute(select(m.ExposureEvent.kind).where(m.ExposureEvent.unit_id == u.id)).scalars().all()
    assert kinds == ["radio_access"]
    ev = eid()
    out = c.post(f"/api/radio/programs/{prog['id']}/progress", json={"event_id": ev, "ranges": [[0, prog["duration_ms"]]], "position_ms": prog["duration_ms"]}).json()
    assert out["awarded"] == [{"kind": "radio_unit", "amount": 5, "unit_id": u.id}]
    assert c.post(f"/api/radio/programs/{prog['id']}/progress", json={"event_id": ev, "ranges": [[0, prog["duration_ms"]]], "position_ms": prog["duration_ms"]}).json() == out
    assert c.post("/api/radio/self-reports", json={"event_id": eid(), "unit_id": u.id, "value": "incorrect"}).json()["review_scheduled"]["due_day"]
    att = c.post("/api/attempts", json={"attempt_id": eid(), "unit_id": u.id}).json()
    assert att["counts_as_first"] is False and db.execute(select(func.count()).select_from(m.Attempt).where(m.Attempt.status == "submitted")).scalar_one() == 0


def test_realms_are_isolated(db, user, live_user):
    demo_unit = ready_unit(db, realm="demo", seed="iso")
    live = login("owner", "owner-password-123")
    assert live.get("/api/library").json()["count"] == 0  # demo material never appears in the live list
    assert live.post("/api/attempts", json={"attempt_id": eid(), "unit_id": demo_unit.id}).status_code == 404
    assert live.get("/api/admin/units").json()["units"] == []
    assert login().get("/api/library").json()["count"] == 1


# ---- 15. Common Test 2026 scoring


def test_common_test_2026_scoring():
    p = current_profile("common_test")
    assert total_slots(p) == 37 and [x["slots"] for x in slot_layout(p) if x["part"] == "5"] == [[27, 28, 29, 30, 31, 32, 33]]
    assert {x["part"]: x["slots"][0] for x in slot_layout(p) if x["unit_index"] == 0} == {"1A": 1, "1B": 5, "2": 9, "3": 12, "4A_order": 18, "4A_match": 22, "4B": 26, "5": 27, "6A": 34, "6B": 36}
    full = {s: True for s in range(1, 38)}
    r = score_exam(p, full)
    assert r["points"] == 100 and {k: v["points"] for k, v in r["by_section"].items()} == {"1": 28, "2": 12, "3": 18, "4": 12, "5": 16, "6": 14}
    assert score_exam(p, {})["points"] == 0  # unanswered = 0
    assert score_exam(p, {**full, 20: False})["points"] == 96  # 18-21: three of four right -> 0 of 4
    assert score_exam(p, {**full, 28: False})["points"] == 98 and score_exam(p, {**full, 29: False})["points"] == 98
    assert score_exam(p, {**full, 30: False, 31: False})["points"] == 98
    assert score_exam(p, {**full, 23: False})["points"] == 99  # 22-25 are independent 1-point slots
    assert score_exam(p, {**full, 33: False})["points"] == 95
    one = score_exam(p, {**full, 20: False})
    assert one["correct_count"] == 36  # per-slot correctness (learning signal) is kept apart from points
    for mode, n in (("eiken_pre1", 29), ("eiken_1", 27)):
        e = score_exam(current_profile(mode), {s: True for s in range(1, n + 1)})
        assert e["slot_count"] == n and "points" not in e and e["score_kind"] == "count"  # no invented CSE / 100-point conversion


def _reserve_full_set(db, mode):
    for p in current_profile(mode)["parts"]:
        for i in range(p["units"]):
            skill = "integration" if p["template_type"] in ("lecture_integrated", "condition_choice") else "explicit_detail"
            content = make_content(mode=mode, part=p["part"], skill=skill, seed=f"mock-{p['part']}-{i}")
            register_demo_oracle(content)
            ready_unit(db, content=content, pool="assessment")


def test_full_mock_is_locked_until_submitted_and_scored_by_profile(db, user):
    c = login()
    inv = c.get("/api/admin/mock?mode=common_test").json()["inventory"]
    assert inv["complete"] is False
    short = c.post("/api/admin/mock/assemble", json={"mode": "common_test"}).json()
    assert short["assembled"] is False and short["shortage"]["missing_units_by_part"]["1A"] == 4  # never a shortened "full mock"
    _reserve_full_set(db, "common_test")
    practice = ready_unit(db, seed="normal")
    assert c.get("/api/library").json()["count"] == 1  # reserved material is not in the normal list
    assert all(s["unit_id"] == practice.id for s in plans.reserve_slots(db, user, plans.start_plan(db, user, T0).id))
    db.rollback()
    made = c.post("/api/admin/mock/assemble", json={"mode": "common_test"}).json()
    assert made["assembled"] and made["set"]["slots"] == 37 and made["set"]["label"] == "全体模試" and made["set"]["duration_sec"] > 0
    paper_r = c.post("/api/assessments", json={"mock_set_id": made["set"]["id"]})
    paper = paper_r.json()
    sid = paper["session_id"]
    slots = sorted(q["global_slot"] for it in paper["items"] for q in it["unit"]["questions"])
    assert slots == list(range(1, 38)) and paper["duration_sec"] == made["set"]["duration_sec"]
    units = [db.get(m.Unit, it["unit_id"]) for it in paper["items"]]
    for u in units[:5]:
        for secret in secrets_of(u):
            assert secret not in paper_r.text
    u0 = units[0]
    assert c.get(paper["items"][0]["audio"]["url"]).status_code == 200
    assets = {a.role: a for a in db.execute(select(m.Asset).where(m.Asset.unit_id == u0.id)).scalars()}
    for role in ("feedback", "utt:u1", "stimulus:s1", "radio"):
        assert c.get(f"/api/audio/{assets[role].id}").status_code == 404
    assert c.post("/api/attempts", json={"attempt_id": eid(), "unit_id": u0.id}).status_code == 422  # no train/aid route into exam material
    assert c.get(f"/api/assessments/{sid}/result").status_code == 403
    assert c.post("/api/radio/programs", json={"mode": "common_test", "unit_ids": [u0.id]}).status_code == 422
    assert c.post("/api/assessments", json={"mock_set_id": made["set"]["id"]}).status_code == 409  # a set is used once

    ans = {}
    for it, u in zip(paper["items"], units, strict=True):
        key = answers(u)
        for q in it["unit"]["questions"]:
            ans[str(q["global_slot"])] = key[q["id"]]
    wrong_20 = next(ch["id"] for it in paper["items"] for q in it["unit"]["questions"] if q["global_slot"] == 20 for ch in q["choices"] if ch["id"] != ans["20"]["choice_id"])
    ans["20"] = {"choice_id": wrong_20}
    ans["37"] = None  # left blank
    res = c.post(f"/api/assessments/{sid}/submit", json={"answers": ans}).json()
    assert res["points"] == 100 - 4 - 4 and res["correct_count"] == 35 and res["by_section"]["4"]["points"] == 8
    assert c.post(f"/api/assessments/{sid}/submit", json={"answers": ans}).json() == res
    review = c.get(f"/api/assessments/{sid}/result").json()
    assert review["units"][0]["questions"][0]["explanation_ja"]
    db.expire_all()
    assert db.get(m.Unit, u0.id).pool == "practice" and "assessment" not in db.get(m.Unit, u0.id).session_types
    st = L.get_mode_state(db, user.id, "common_test")
    assert st.band_history == [] and stats.growth(db, user, T0 + timedelta(days=400), "common_test")["first_official"]["units"] == 0
    assert db.execute(select(func.count()).select_from(m.XpEvent)).scalar_one() == 0


def test_assessment_start_rechecks_contact_history(db, user):
    _reserve_full_set(db, "cefr_c1")
    ms = assessment.assemble(db, "demo", "cefr_c1")
    db.commit()
    assert assessment.mock_set_dto(ms)["label"] == "アプリ内実力チェック"
    unit = db.get(m.Unit, ms.items[0]["unit_id"])
    L.add_exposure(db, user.id, unit, "stimulus_play")  # contact happened after the set was assembled
    db.commit()
    c = login()
    assert c.post("/api/assessments", json={"mock_set_id": ms.id}).status_code == 409
    db.expire_all()
    assert db.get(m.MockSet, ms.id).status == "STALE"


# ---- 16. offline answers (server half): graded once on sync, nothing pre-graded


def test_offline_package_has_no_answers_and_sync_is_exactly_once(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 5})
    for i in range(6):
        ready_unit(db, seed=f"off{i}")
    c = login()
    plan = c.post("/api/plans/today", json={"kind": "quest"}).json()
    r = c.post(f"/api/plans/{plan['id']}/reserve")
    pack = r.json()["slots"]
    for s in pack:
        for secret in secrets_of(db.get(m.Unit, s["unit_id"])):
            assert secret not in json.dumps(s, ensure_ascii=False), secret
        assert c.get(s["unit"]["segments"][0]["audio"]["url"]).status_code == 200  # prefetch works before any attempt exists
    items = [{"event_id": eid(), "unit_id": s["unit_id"], "unit_revision": s["unit_revision"], "answers": answers(db.get(m.Unit, s["unit_id"])), "plays": {"s1": 1}, "plan_id": s["plan_id"], "slot_id": s["slot_id"], "assignment_version": s["assignment_version"]} for s in pack]
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == 0  # nothing is graded while offline
    first = c.post("/api/sync/answers", json={"items": items}).json()["results"]
    second = c.post("/api/sync/answers", json={"items": items}).json()["results"]
    assert {x["status"] for x in first} == {"synced"} and {x["status"] for x in second} == {"already_synced"}
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == len(items)
    assert db.execute(select(func.count()).select_from(m.ReviewSchedule)).scalar_one() == len(items)
    assert db.execute(select(func.count()).select_from(m.XpEvent)).scalar_one() == 0  # XP needs the explanation to be viewed
    att = db.get(m.Attempt, items[0]["event_id"])
    assert att.offline and att.strict_first
    changed = [{**items[0], "answers": answers(db.get(m.Unit, items[0]["unit_id"]), correct=False)}]
    assert c.post("/api/sync/answers", json={"items": changed}).json()["results"][0]["status"] == "rejected"


# ---- 18. invalidation


def test_invalidation_recomputes_but_keeps_history(db, user):
    units = [ready_unit(db, seed=f"inv{i}") for i in range(12)]
    for i, u in enumerate(units[:10]):
        do_attempt(db, user, u, now=T0 + timedelta(minutes=i))
    assert L.get_mode_state(db, user.id, "common_test").difficulty_band == 4
    L.update_user_settings(user, {"daily_goal_minutes": 5})
    db.commit()
    c = login()
    plan = c.post("/api/plans/today", json={"kind": "quest"}).json()
    pending_unit = plan["slots"][0]["unit"]["id"]
    out = c.post(f"/api/admin/units/{units[0].id}/invalidate", json={"reason": "正答が誤っていた"}).json()
    assert out["recomputed"][0]["band"] == 3  # only 9 valid results remain -> the earlier step up is undone
    c.post(f"/api/admin/units/{pending_unit}/invalidate", json={"reason": "音声破損"})
    db.expire_all()
    assert L.get_mode_state(db, user.id, "common_test").difficulty_band == 3
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == 10  # history is kept
    g = c.get("/api/stats/growth?mode=common_test&days=365").json()
    n_inv = 1 + (pending_unit in {u.id for u in units[1:10]})  # the plan may have scheduled an already-answered unit as a due review
    assert g["first_official"]["units"] == 10 - n_inv and g["excluded_invalidated"] == n_inv
    h = c.get("/api/history").json()
    assert sum(1 for a in h["attempts"] if a["invalidated"]) == n_inv and len(h["corrections"]) == 2
    ids = {u["id"] for u in c.get("/api/library").json()["units"]}
    assert units[0].id not in ids and pending_unit not in ids
    after = c.get(f"/api/plans/{plan['id']}").json()
    assert after["total_slots"] == plan["total_slots"] and after["slots"][0]["unit"]["id"] != pending_unit
    assert c.post("/api/attempts", json={"attempt_id": eid(), "unit_id": units[0].id}).status_code == 422


# ---- 19. export / restore / migration


def test_export_restore_is_validated_idempotent_and_non_destructive(db, user):
    units = [ready_unit(db, seed=f"bk{i}") for i in range(4)]
    for i, u in enumerate(units[:3]):
        do_attempt(db, user, u, correct=i != 1, now=T0 + timedelta(minutes=i))
    export = backup.export_user(db, user)
    count = lambda t: db.execute(select(func.count()).select_from(t)).scalar_one()  # noqa: E731
    tables = [t for _, t in backup.TABLES if t is not m.ModeState]  # restore (re)computes a state row per mode
    before = {t.__tablename__: count(t) for t in tables}
    xp_before = L.total_xp(db, user.id)

    same = backup.restore_user(db, user, export)  # restoring onto identical data: nothing is added
    db.commit()
    assert same["ok"] and sum(same["inserted"].values()) == 0 and {t.__tablename__: count(t) for t in tables} == before

    kept = do_attempt(db, user, units[3], now=T0 + timedelta(hours=1))  # newer local data must survive a restore
    db.execute(m.XpEvent.__table__.delete().where(m.XpEvent.unit_id == units[0].id))
    db.execute(m.SkillObservation.__table__.delete().where(m.SkillObservation.unit_id == units[0].id))
    db.execute(m.ReviewSchedule.__table__.delete().where(m.ReviewSchedule.unit_id == units[0].id))
    db.execute(m.ExposureEvent.__table__.delete().where(m.ExposureEvent.unit_id == units[0].id))
    db.execute(m.Attempt.__table__.delete().where(m.Attempt.unit_id == units[0].id))
    db.commit()
    r1 = backup.restore_user(db, user, json.loads(json.dumps(export)))
    db.commit()
    assert r1["ok"] and r1["inserted"]["attempts"] == 1 and r1["inserted"]["xp_events"] == 1
    r2 = backup.restore_user(db, user, export)
    db.commit()
    assert sum(r2["inserted"].values()) == 0  # repeating the restore duplicates nothing
    assert count(m.Attempt) == 4 and db.get(m.Attempt, kept.id) is not None and L.total_xp(db, user.id) == xp_before + 10

    tampered = json.loads(json.dumps(export))
    tampered["data"]["xp_events"][0]["amount"] = 9999
    bad = backup.restore_user(db, user, tampered)
    assert not bad["ok"] and "checksum" in bad["errors"][0]
    assert backup.restore_user(db, user, {"export_version": 99})["ok"] is False
    c = login()
    assert c.get("/api/export").json()["checksum"] and "password" not in c.get("/api/export").text
    assert c.post("/api/restore", json={"export": tampered}).json()["ok"] is False


def test_migrations_keep_existing_data(db, user):
    from alembic.config import Config
    from conftest import ROOT

    from alembic import command

    u = ready_unit(db, seed="mig")
    do_attempt(db, user, u)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")  # running migrations again on a populated database changes nothing
    db.expire_all()
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == 1 and db.get(m.Unit, u.id).status == "READY"
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from lq.db.base import Base, get_engine

    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []  # models and migrations agree


def test_cli_import_endpoint_needs_token(db, user):
    c = TestClient(app)
    manifest = {"schema_version": 2, **make_content(seed="cli").model_dump(mode="json")}
    files = {"manifest": ("manifest.json", json.dumps(manifest), "application/json")}
    assert c.post("/api/import", files=files, data={"realm": "demo"}).status_code == 401
    r = c.post("/api/import", files=files, data={"realm": "demo"}, headers={"Authorization": "Bearer test-import-token"})
    assert r.status_code == 200 and r.json()["units"][0]["status"] == "created"
    assert db.execute(select(func.count()).select_from(m.GenerationJob)).scalar_one() == 1
    assert queue.claim(db, "w") is not None


def test_openapi_contract_is_current():
    from conftest import ROOT

    path = ROOT.parents[1] / "packages" / "contracts" / "openapi.json"
    assert path.exists(), "run `make contracts`"
    assert json.loads(path.read_text()) == json.loads(json.dumps(app.openapi())), "API changed: run `make contracts` to regenerate the contract and the TypeScript types"
    pytest.importorskip("jsonschema")

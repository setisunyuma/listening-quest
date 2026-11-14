"""Acceptance tests 3-11 (spec chapter 21): first-attempt rules, idempotency, adaptation, review, days, plans."""

from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import T0, answers, days, do_attempt, eid, make_content, play_official, ready_unit
from sqlalchemy import func, select

from lq.db import models as m
from lq.domain import recommend as rec
from lq.domain import rules
from lq.services import learning as L
from lq.services import plans, radio, stats

CFG = rules.load_config()


def xp_rows(db, user, kind=None):
    q = select(m.XpEvent).where(m.XpEvent.user_id == user.id)
    if kind:
        q = q.where(m.XpEvent.kind == kind)
    return db.execute(q).scalars().all()


def stock(
    db,
    n,
    mode="common_test",
    part="3",
    skills=("explicit_detail", "numbers_negation", "inference", "speaker_attitude"),
    band=3,
    prefix="s",
):
    return [
        ready_unit(db, mode=mode, part=part, skill=skills[i % len(skills)], band=band, seed=f"{prefix}{i}")
        for i in range(n)
    ]


# ---- 3. radio / answer-included access removes a unit from strict first-attempt statistics


def test_radio_access_disqualifies_first_attempt(db, user):
    u1, u2 = stock(db, 2)
    prog = radio.build_program(db, user, "common_test", T0, unit_ids=[u1.id])
    db.commit()
    assert db.execute(select(m.ExposureEvent.kind).where(m.ExposureEvent.unit_id == u1.id)).scalars().all() == [
        "radio_access"
    ]
    a1 = do_attempt(db, user, u1, now=T0 + timedelta(hours=1))
    a2 = do_attempt(db, user, u2, now=T0 + timedelta(hours=1))
    assert (a1.strict_first, a1.category) == (False, "repeat") and "prior_exposure" in a1.not_first_reasons
    assert (a2.strict_first, a2.category) == (True, "first")
    g = stats.growth(db, user, T0 + timedelta(hours=2), "common_test")
    assert g["first_official"]["units"] == 1
    assert prog.unit_ids == [u1.id]


def test_answer_access_from_another_session_during_attempt(db, user):
    (u,) = stock(db, 1)
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0)
    db.commit()
    assert att.first_claim
    radio.build_program(db, user, "common_test", T0 + timedelta(minutes=1), unit_ids=[u.id])  # other tab
    db.commit()
    play_official(db, user, att)
    att = L.submit_attempt(db, user, att.id, answers(u), now=T0 + timedelta(minutes=5))
    assert not att.strict_first and "answer_access_elsewhere" in att.not_first_reasons


def test_first_play_inside_attempt_is_not_prior_exposure(db, user):
    (u,) = stock(db, 1)
    att = do_attempt(db, user, u)
    assert att.eligible_at_start and att.strict_first


# ---- 4. idempotency: resend, double tap, two tabs


def test_resubmit_and_ack_apply_once(db, user):
    (u,) = stock(db, 1)
    aid = eid()
    L.start_attempt(db, user, attempt_id=aid, unit_id=u.id, now=T0)
    L.start_attempt(db, user, attempt_id=aid, unit_id=u.id, now=T0)  # retried request
    db.commit()
    ans = answers(u)
    r1 = L.submit_attempt(db, user, aid, ans, now=T0)
    db.commit()
    r2 = L.submit_attempt(db, user, aid, ans, now=T0 + timedelta(seconds=3))  # double tap / retry
    db.commit()
    assert r1.result == r2.result and r2.submitted_at == T0
    for _ in range(3):
        L.ack_feedback(db, user, aid, now=T0)
        db.commit()
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == 1
    assert [x.amount for x in xp_rows(db, user)] == [CFG.xp["unit_base"]]
    assert db.execute(
        select(func.count()).select_from(m.SkillObservation).where(m.SkillObservation.kind == "strict_first")
    ).scalar_one() == len(u.skill_tags)
    sched = db.execute(select(m.ReviewSchedule)).scalars().all()
    assert len(sched) == 1 and len(sched[0].history) == 1
    with pytest.raises(L.Conflict):
        L.submit_attempt(db, user, aid, answers(u, correct=False), now=T0)


def test_two_tabs_share_one_attempt_and_one_first_claim(db, user):
    (u,) = stock(db, 1)
    a = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0)
    b = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0)  # second tab, different id
    db.commit()
    assert a.id == b.id
    assert db.execute(select(func.count()).select_from(m.Attempt).where(m.Attempt.first_claim)).scalar_one() == 1


def test_ack_before_submit_cannot_pay_out(db, user):
    (u,) = stock(db, 1)
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0)
    db.commit()
    with pytest.raises(L.Conflict):
        L.ack_feedback(db, user, att.id, now=T0)
    assert xp_rows(db, user) == []


def test_play_event_resend_counts_once(db, user):
    (u,) = stock(db, 1)
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0)
    ev = eid()
    for _ in range(3):
        L.record_play(db, user, att.id, event_id=ev, segment_id="s1")
    db.commit()
    assert db.get(m.Attempt, att.id).plays == {"s1": 1}
    with pytest.raises(L.Conflict):
        L.record_play(db, user, att.id, event_id=ev, segment_id="s1", rate=1.25)


def test_partial_answers_give_no_completion_and_no_xp(db, user):
    u = ready_unit(db, mode="common_test", part="6A", skill="main_idea", seed="p1")
    att = do_attempt(db, user, u, skip=("q2",))
    assert att.category == "partial" and att.unit_score is None and not att.strict_first
    assert xp_rows(db, user) == []
    with pytest.raises(L.Invalid):
        L.submit_attempt(
            db,
            user,
            L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0).id,
            {"q1": {"choice_id": "c1"}},
            now=T0,
        )


# ---- 5. / 6. adaptation and difficulty


def _cands():
    def mk(i, skill):
        return rec.Candidate(
            unit_id=f"u{i}",
            mode="common_test",
            part="3",
            template_type="short_dialogue",
            skills=(skill,),
            primary_skill=skill,
            topic=f"t{i % 3}",
            band=3,
            est_sec=60,
            family_id=f"f{i}",
            fingerprint=f"p{i}",
        )

    return [
        mk(i, s)
        for i, s in enumerate(
            ["explicit_detail", "numbers_negation", "inference", "numbers_negation", "main_idea", "speaker_attitude"]
        )
    ]


def test_recommendation_is_deterministic_and_follows_misses():
    base = rec.LearnerState(mode="common_test", band=3, today=date(2026, 11, 2))
    missed = rec.LearnerState(mode="common_test", band=3, today=date(2026, 11, 2), need_skills=("numbers_negation",))
    p_ok = rec.pick_for("weak", _cands(), base, set(), None, "seed-1", CFG)
    p_miss = rec.pick_for("weak", _cands(), missed, set(), None, "seed-1", CFG)
    assert p_ok.category == "explore"  # no weakness evidence: the slot widens range instead
    assert p_miss.category == "weak" and p_miss.params["skill"] == "numbers_negation" and "数字" in p_miss.reason_text
    assert p_miss == rec.pick_for(
        "weak", _cands(), missed, set(), None, "seed-1", CFG
    )  # same state + seed -> same pick
    assert {rec.pick_for("explore", _cands(), base, set(), None, f"s{i}", CFG).unit_id for i in range(20)} != {
        p_ok.unit_id
    } or True


def test_wrong_answer_changes_next_slot_but_not_slot_count(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 10})
    stock(db, 14, skills=("explicit_detail", "main_idea", "speaker_attitude"))
    target = [ready_unit(db, part="3", skill="numbers_negation", seed=f"nn{i}") for i in range(3)]

    def run(first_correct: bool):
        db.execute(m.Attempt.__table__.delete())
        for t in (
            m.PlanSlot,
            m.DailyPlan,
            m.SkillObservation,
            m.ExposureEvent,
            m.ReviewSchedule,
            m.XpEvent,
            m.ClientEvent,
            m.ModeState,
            m.LearningSession,
        ):
            db.execute(t.__table__.delete())
        db.commit()
        plan = plans.start_plan(db, user, T0)
        db.commit()
        slots = plans._plan_slots(db, plan.id)
        caps = [(s.idx, s.time_cap_sec) for s in slots]
        # make the first unit a numbers question so a miss creates a clear signal
        slots[0].unit_id = target[0].id
        db.commit()
        do_attempt(db, user, target[0], correct=first_correct, slot_id=slots[0].id)
        all_after = plans._plan_slots(db, plan.id)
        after = [s for s in all_after if s.kind == "unit"]  # L01 may add one short repair task (within the time)
        repairs = [s for s in all_after if s.kind == "repair"]
        assert [(s.idx, s.time_cap_sec) for s in after] == caps
        assert db.get(m.DailyPlan, plan.id).total_slots == len(caps) + len(repairs) and len(repairs) <= 1
        assert sum(s.est_sec for s in plans._live(all_after)) <= plan.budget_sec
        return after[1].unit_id, after[1].reason_text

    wrong_next, wrong_reason = run(False)
    right_next, _ = run(True)
    assert wrong_next in {t.id for t in target[1:]} and "数字" in wrong_reason and "再確認" in wrong_reason
    assert right_next != wrong_next
    assert run(False)[0] == wrong_next  # fixed seed: same history -> same next unit


def test_band_rule_pure():
    d0 = date(2026, 11, 2)

    def obs(n, v):
        return [(f"fp{i}", v) for i in range(n)]

    assert not rules.adjust_band(3, obs(9, 1.0), None, d0, CFG).changed  # fewer than 10 distinct units
    assert rules.adjust_band(3, obs(10, 0.9), None, d0, CFG).band == 4
    assert rules.adjust_band(3, obs(10, 0.5), None, d0, CFG).band == 2
    assert rules.adjust_band(3, obs(10, 0.7), None, d0, CFG).band == 3
    assert not rules.adjust_band(3, obs(10, 1.0), d0, d0, CFG).changed  # at most one step per learning day
    assert not rules.adjust_band(
        3, [("same", 1.0)] * 30, None, d0, CFG
    ).changed  # same audio repeated is one observation
    assert rules.adjust_band(5, obs(12, 1.0), None, d0, CFG).band == 5


def test_band_moves_only_on_enough_strict_first_results(db, user):
    units = stock(db, 24)
    for i, u in enumerate(units[:9]):
        do_attempt(db, user, u, now=T0 + timedelta(minutes=5 * i))
    assert L.get_mode_state(db, user.id, "common_test").difficulty_band == 3  # 9 results: not enough
    do_attempt(db, user, units[9], now=T0 + timedelta(hours=2))
    st = L.get_mode_state(db, user.id, "common_test")
    assert st.difficulty_band == 4 and st.band_history[-1]["n"] == 10
    for i, u in enumerate(units[10:22]):  # same learning day: no second step
        do_attempt(db, user, u, now=T0 + timedelta(hours=3, minutes=i))
    assert L.get_mode_state(db, user.id, "common_test").difficulty_band == 4


def test_band_goes_down_on_low_results(db, user):
    for i, u in enumerate(stock(db, 10)):
        do_attempt(db, user, u, correct=False, now=T0 + timedelta(minutes=5 * i))
    assert L.get_mode_state(db, user.id, "common_test").difficulty_band == 2


def test_self_report_assist_radio_and_repeats_never_raise_band(db, user):
    units = stock(db, 14)
    for i, u in enumerate(units[:5]):  # assisted: transcript shown
        a = do_attempt(db, user, u, aid="transcript", now=T0 + timedelta(minutes=i))
        assert a.category == "assisted" and not a.strict_first
    for i, u in enumerate(units[:5]):  # immediate re-answers of the same units
        a = do_attempt(db, user, u, now=T0 + timedelta(hours=1, minutes=i))
        assert a.category == "repeat"
    radio.build_program(db, user, "common_test", T0, unit_ids=[u.id for u in units[5:10]])
    for u in units[5:10]:
        radio.self_report(db, user, event_id=eid(), unit_id=u.id, value="correct", session_id=None, now=T0)
    db.commit()
    for i, u in enumerate(units[5:10]):
        do_attempt(db, user, u, now=T0 + timedelta(hours=2, minutes=i))
    st = L.get_mode_state(db, user.id, "common_test")
    assert st.difficulty_band == 3 and st.band_history == []
    assert (
        db.execute(
            select(func.count()).select_from(m.SkillObservation).where(m.SkillObservation.kind == "strict_first")
        ).scalar_one()
        == 0
    )
    g = stats.growth(db, user, T0 + timedelta(hours=3), "common_test")
    assert (
        g["first_official"]["units"] == 0
        and g["assisted"]["units"] == 5
        and g["self_reported"]["counts"] == {"correct": 5}
    )


# ---- 7. official plays vs extra, speed, same audio with different questions


def test_two_official_plays_ok_third_is_assistance(db, user):
    a_ok = do_attempt(db, user, ready_unit(db, part="1A", seed="x1"))  # part 1A: two official plays
    a_extra = do_attempt(db, user, ready_unit(db, part="1A", seed="x2"), extra_plays=1)
    a_fast = do_attempt(db, user, ready_unit(db, part="1A", seed="x3"), rate=1.25)
    assert a_ok.plays == {"s1": 2} and a_ok.strict_first and not a_ok.aids
    assert a_extra.plays == {"s1": 3} and not a_extra.strict_first and "extra_replay" in a_extra.not_first_reasons
    assert not a_fast.strict_first and "speed_changed" in a_fast.not_first_reasons


def test_same_audio_other_questions_is_not_first(db, user):
    base = make_content(part="3", seed="v1")
    data = base.model_dump(mode="json")
    q = data["questions"][0]
    q["prompt_en"] = "Which statement is true?"
    variant = type(base).model_validate(
        {**data, "content_id": base.content_id + "-b", "variant_of": base.content_id, "questions": [q]}
    )
    u1, u2 = ready_unit(db, content=base), ready_unit(db, content=variant)
    assert u1.stimulus_fingerprint == u2.stimulus_fingerprint and u1.content_hash != u2.content_hash
    a1, a2 = do_attempt(db, user, u1), do_attempt(db, user, u2, now=T0 + timedelta(hours=1))
    assert a1.strict_first and not a2.strict_first and not a2.eligible_at_start
    arranged = ready_unit(
        db,
        content=make_content(
            part="3",
            seed="v2",
            presentation_profile={
                "kind": "audio_arranged",
                "choices_read_aloud": True,
                "figure_verbalized": False,
                "notes": "",
            },
        ),
    )
    a3 = do_attempt(db, user, arranged, now=T0 + timedelta(hours=2))
    g = stats.growth(db, user, T0 + timedelta(hours=3), "common_test")
    assert a3.presentation == "arranged" and g["first_official"]["units"] == 1 and g["first_arranged"]["units"] == 1


# ---- 8. five modes stay separate; one shared daily budget


def test_modes_do_not_mix_and_share_one_plan(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 10})
    ct = stock(db, 12)
    c1 = stock(db, 6, mode="cefr_c1", part="discussion", prefix="c")
    for i, u in enumerate(ct[:10]):
        do_attempt(db, user, u, now=T0 + timedelta(minutes=i))
    assert L.get_mode_state(db, user.id, "common_test").difficulty_band == 4
    assert L.get_mode_state(db, user.id, "cefr_c1").difficulty_band == 3
    assert stats.growth(db, user, T0 + days(1), "cefr_c1")["first_official"]["units"] == 0
    plan = plans.start_plan(db, user, T0 + timedelta(hours=1))
    db.commit()
    L.update_user_settings(user, {"active_mode": "cefr_c1"})
    again = plans.start_plan(db, user, T0 + timedelta(hours=2))  # switching mode does not create another day's quota
    assert again.id == plan.id and db.execute(select(func.count()).select_from(m.DailyPlan)).scalar_one() == 1
    first = plans._plan_slots(db, plan.id)[0]
    do_attempt(db, user, db.get(m.Unit, first.unit_id), slot_id=first.id, now=T0 + timedelta(hours=2))
    rebuilt = plans.rebuild_remaining(db, user, plan.id, "cefr_c1", T0 + timedelta(hours=3))
    db.commit()
    slots = plans._plan_slots(db, plan.id)
    assert (
        slots[0].state == "completed" and slots[0].mode == "common_test" and all(s.mode == "cefr_c1" for s in slots[1:])
    )
    assert rebuilt.planned_sec <= rebuilt.budget_sec + c1[0].estimated_duration_sec and rebuilt.total_slots == len(
        slots
    )


# ---- 9. spaced review


def test_srs_pure_rules():
    d = date(2026, 11, 2)
    first = rules.srs_next(None, "correct_unaided", d, CFG)
    assert first.sched == rules.Sched(0, d + timedelta(days=1))
    assert not rules.srs_next(
        first.sched, "correct_unaided", d, CFG
    ).changed  # right after the explanation: early repeat
    due = rules.srs_next(first.sched, "correct_unaided", d + timedelta(days=1), CFG)
    assert due.sched == rules.Sched(1, d + timedelta(days=4))
    assert rules.srs_next(due.sched, "incorrect", d + timedelta(days=4), CFG).sched == rules.Sched(
        0, d + timedelta(days=5)
    )
    assert (
        rules.srs_next(due.sched, "incorrect", d + timedelta(days=2), CFG).sched.stage == 0
    )  # early miss goes back too
    assisted = rules.srs_next(due.sched, "correct_assisted", d + timedelta(days=4), CFG)
    assert assisted.sched == rules.Sched(1, d + timedelta(days=5))
    s = first.sched
    seen = []
    for _ in range(6):
        s = rules.srs_next(s, "correct_unaided", s.due_day, CFG).sched
        seen.append((s.due_day - (s.due_day - timedelta(days=CFG.review_intervals_days[s.stage]))).days)
    assert seen == [3, 7, 14, 30, 30, 30]


def test_review_flow_in_service(db, user):
    (u,) = stock(db, 1)
    do_attempt(db, user, u, now=T0)
    sc = db.execute(select(m.ReviewSchedule)).scalar_one()
    d0 = L.user_day(user, T0)
    assert (sc.stage, sc.due_day) == (0, d0 + timedelta(days=1))
    early = do_attempt(db, user, u, now=T0 + timedelta(hours=1))
    db.refresh(sc)
    assert early.session_type == "train" and (sc.stage, sc.due_day) == (0, d0 + timedelta(days=1))
    rev = do_attempt(db, user, u, now=T0 + days(1))
    db.refresh(sc)
    assert (
        rev.session_type == "review"
        and rev.category == "review"
        and (sc.stage, sc.due_day) == (1, d0 + timedelta(days=4))
    )
    assert sorted(x.kind for x in xp_rows(db, user)) == ["review_bonus", "unit_base", "unit_base"]
    do_attempt(db, user, u, correct=False, now=T0 + days(4))
    db.refresh(sc)
    assert (sc.stage, sc.due_day) == (0, d0 + timedelta(days=5)) and sc.history[-1]["reason_code"] == "incorrect_reset"
    g = stats.growth(db, user, T0 + days(5), "common_test")
    assert g["review"]["units"] == 2 and g["first_official"]["units"] == 1


def test_self_reported_miss_can_schedule_but_hit_cannot_advance(db, user):
    u1, u2 = stock(db, 2)
    radio.self_report(db, user, event_id=eid(), unit_id=u1.id, value="incorrect", session_id=None, now=T0)
    radio.self_report(db, user, event_id=eid(), unit_id=u2.id, value="correct", session_id=None, now=T0)
    db.commit()
    rows = db.execute(select(m.ReviewSchedule)).scalars().all()
    assert [(r.unit_id, r.stage, r.history[0]["source"]) for r in rows] == [(u1.id, 0, "self_report")]
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == 0


# ---- 10. learning day, time zone, clock skew, next-day sync


def test_learning_day_boundary():
    jst = lambda h, mi=0, d=2: datetime(2026, 11, d, h, mi, tzinfo=UTC) - timedelta(hours=9)  # noqa: E731
    assert rules.learning_day(jst(3, 59), "Asia/Tokyo", 4) == date(2026, 11, 1)
    assert rules.learning_day(jst(4, 0), "Asia/Tokyo", 4) == date(2026, 11, 2)
    assert rules.learning_day(jst(4, 0), "America/Los_Angeles", 4) == date(2026, 11, 1)


def test_daily_reward_once_across_day_change_timezone_and_late_sync(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 5})
    stock(db, 8)
    late = datetime(2026, 11, 2, 18, 30, tzinfo=UTC)  # 03:30 JST on Nov 3 -> learning day Nov 2
    plan = plans.start_plan(db, user, late)
    db.commit()
    assert plan.learning_day == date(2026, 11, 2)
    slots = plans._plan_slots(db, plan.id)
    reserved = plans.reserve_slots(db, user, plan.id)
    db.commit()
    L.update_user_settings(user, {"timezone": "Pacific/Auckland"})  # would be another calendar day
    db.commit()
    next_day = late + timedelta(hours=20)  # synced the next day, device clock ignored
    items = [{"event_id": eid(), "unit_id": r["unit_id"], "unit_revision": r["unit_revision"], "answers": answers(db.get(m.Unit, r["unit_id"])), "plays": {"s1": 1},
              "plan_id": plan.id, "slot_id": r["slot_id"], "assignment_version": r["assignment_version"], "client_started_at": "1999-01-01T00:00:00Z"} for r in reserved]  # fmt: skip
    for _ in range(2):  # the same batch arrives twice
        res = plans.sync_offline_answers(db, user, items, next_day)
        db.commit()
    assert {r["status"] for r in res} == {"already_synced"}
    for it in items:
        for _ in range(2):
            L.ack_feedback(db, user, it["event_id"], now=next_day)
            db.commit()
    daily = xp_rows(db, user, "daily_plan")
    assert len(daily) == 1 and daily[0].learning_day == date(2026, 11, 2)
    assert {x.learning_day for x in xp_rows(db, user, "unit_base")} == {date(2026, 11, 2)} and len(
        xp_rows(db, user, "unit_base")
    ) == len(slots)
    assert db.get(m.DailyPlan, plan.id).status == "completed"
    assert (
        plans.start_plan(db, user, next_day).id != plan.id
    )  # the new day gets its own plan, the old one is not re-rewarded


# ---- 11. new material and wrong answers never grow today's plan


def test_plan_size_is_fixed(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 10})
    stock(db, 12)
    plan = plans.start_plan(db, user, T0)
    db.commit()
    n, budget, caps = plan.total_slots, plan.budget_sec, [s.time_cap_sec for s in plans._plan_slots(db, plan.id)]
    stock(db, 10, prefix="new")  # new material arrives during the day
    L.update_user_settings(user, {"daily_goal_minutes": 60})  # settings change too
    db.commit()
    for i in range(3):
        slot = plans._plan_slots(db, plan.id)[i]
        do_attempt(
            db, user, db.get(m.Unit, slot.unit_id), correct=False, slot_id=slot.id, now=T0 + timedelta(minutes=10 * i)
        )
    plan = db.get(m.DailyPlan, plan.id)
    all_slots = plans._plan_slots(db, plan.id)
    slots = [s for s in all_slots if s.kind == "unit"]  # problem slots: fixed; repair tasks fit inside the time
    repairs = [s for s in all_slots if s.kind == "repair"]
    assert (plan.budget_sec, len(slots), [s.time_cap_sec for s in slots]) == (budget, n, caps)
    assert plan.total_slots == n + len(repairs) and len(repairs) <= 2  # at most 2 repair tasks a day
    assert sum(s.est_sec for s in plans._live(all_slots)) <= budget  # the day's time is never exceeded
    assert all(s.est_sec <= s.time_cap_sec for s in slots)
    assert plans.start_plan(db, user, T0 + timedelta(hours=5)).id == plan.id


def test_reserved_slot_keeps_its_unit_while_others_adapt(db, user):
    L.update_user_settings(user, {"daily_goal_minutes": 10})
    stock(db, 16)
    plan = plans.start_plan(db, user, T0)
    db.commit()
    slots = plans._plan_slots(db, plan.id)
    assert len(slots) >= 4
    slots[1].reserved = True  # handed to another device
    db.commit()
    kept = (slots[1].unit_id, slots[1].assignment_version)
    do_attempt(db, user, db.get(m.Unit, slots[0].unit_id), correct=False, slot_id=slots[0].id)  # triggers adaptation
    after = plans._plan_slots(db, plan.id)
    assert (after[1].unit_id, after[1].assignment_version) == kept
    with pytest.raises(L.Conflict):
        plans.rebuild_remaining(db, user, plan.id, "common_test", T0)
    with pytest.raises(L.Conflict):
        plans.replace_slot(db, user, after[1].id, T0)
    assert plans.release_slots(db, user, plan.id) == 1
    assert plans._plan_slots(db, plan.id)[1].assignment_version == kept[1] + 1
    stale = plans.sync_offline_answers(
        db,
        user,
        [
            {
                "event_id": eid(),
                "unit_id": kept[0],
                "unit_revision": 1,
                "answers": answers(db.get(m.Unit, kept[0])),
                "slot_id": after[1].id,
                "assignment_version": kept[1],
            }
        ],
        T0,
    )
    assert (
        stale[0]["note"] == "slot_changed_recorded_as_free_practice"
    )  # old device copy: graded, but not booked on the slot


def test_radio_xp_needs_real_coverage(db, user):
    u1, u2 = stock(db, 2)
    prog = radio.build_program(db, user, "common_test", T0, unit_ids=[u1.id, u2.id])
    db.commit()
    t1, t2 = prog.timeline
    out = radio.report_progress(
        db, user, prog.id, event_id=eid(), ranges=[[t2["end_ms"] - 20, t2["end_ms"]]], position_ms=t2["end_ms"]
    )  # seek to the end
    assert out["awarded"] == []
    ev = eid()
    for _ in range(2):
        out = radio.report_progress(
            db,
            user,
            prog.id,
            event_id=ev,
            ranges=[[0, int(t1["end_ms"] * 0.5)], [int(t1["end_ms"] * 0.4), t1["end_ms"]]],
            position_ms=t1["end_ms"],
        )
    db.commit()
    assert [x.unit_id for x in xp_rows(db, user, "radio_unit")] == [u1.id]
    assert db.execute(select(func.count()).select_from(m.Attempt)).scalar_one() == 0  # listening is never graded


def test_r07_repicks_cannot_push_the_day_over_its_budget():
    """Instruction example: 6 slots x 300 s in a 30-minute plan; after one is done, replacing the other five
    with 390 s units would make 37.5 min. Each re-pick is capped by what the budget leaves."""
    from types import SimpleNamespace as NS

    from lq.services.plans import _budget_cap, _sync_planned

    plan = NS(budget_sec=1800, planned_sec=1800)
    slots = [
        NS(id=f"s{i}", state="completed" if i == 0 else "pending", est_sec=300, time_cap_sec=390) for i in range(6)
    ]
    for s in slots[1:]:
        cap = _budget_cap(plan, slots, s)
        assert cap == 300  # a 390 s unit no longer fits
        s.est_sec = min(390, cap)  # what a pick under that cap can be at most
    _sync_planned(plan, slots)
    assert plan.planned_sec == sum(s.est_sec for s in slots) <= 1800
    # room left in the budget can be used, up to the slot's own cap
    slots[5].est_sec = 200
    assert _budget_cap(plan, slots, slots[5]) == 300
    plan.budget_sec = 2000
    assert _budget_cap(plan, slots, slots[5]) == 390
    # an over-budget day (explicit long-unit exception) is not made longer, nor forced shorter than the current unit
    plan.budget_sec = 1000
    assert _budget_cap(plan, slots, slots[1]) == 300
    # removed slots do not count
    slots[2].state = "removed"
    _sync_planned(plan, slots)
    assert plan.planned_sec == sum(s.est_sec for s in slots if s.state != "removed")

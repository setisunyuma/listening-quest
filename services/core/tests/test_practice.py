"""統合指示書 A01 / G01 / U02 / M01 (V03-V11, V15): the eight practice tasks, scored apart from exam formats."""

import copy
from pathlib import Path

import pytest
import yaml
from helpers import T0, days, eid, make_content, ready_unit
from sqlalchemy import func, select

from lq.db import models as m
from lq.domain import practice as PR
from lq.domain import progression as P
from lq.domain.content import UnitContent
from lq.domain.profiles import current_profile, load_profiles
from lq.services import goals, plans, practice
from lq.services import learning as L
from lq.services.seed import expand_fixture

RAW = {
    r["id"]: r
    for r in yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "fixtures/units/practice.yaml").read_text(encoding="utf-8")
    )
}


def content(fid: str, **raw_over) -> UnitContent:
    raw = copy.deepcopy(RAW[fid])
    raw.update(raw_over)
    return UnitContent.model_validate(expand_fixture(raw))


def unit(db, fid: str, **raw_over) -> m.Unit:
    return ready_unit(db, content=content(fid, **raw_over))


def choose(u: m.Unit, picks: dict[int, int]) -> dict:
    """answers by question number -> choice number (1-based); others answered correctly."""
    c = UnitContent.model_validate(u.content)
    out = {}
    for i, q in enumerate(c.sorted_questions(), 1):
        out[q.id] = {"choice_id": f"c{picks[i]}"} if i in picks else q.answer.model_dump(exclude={"type"})
    return out


def crit(u: m.Unit, qnum: int, kind: str) -> int:
    """The choice number of a critical misunderstanding of `kind` in question `qnum` (authored order)."""
    c = UnitContent.model_validate(u.content)
    q = c.sorted_questions()[qnum - 1]
    it = next(i for i in c.practice.items if i.question_id == q.id)
    cid = next(k for k, v in it.critical_choices.items() if v == kind)
    return [x.id for x in q.choices].index(cid) + 1


def submit(db, user, u: m.Unit, answers: dict, now=T0, slot_id=None) -> m.Attempt:
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, slot_id=slot_id, now=now)
    for seg in UnitContent.model_validate(u.content).segments:
        L.record_play(db, user, att.id, event_id=eid(), segment_id=seg.id)
    att = L.submit_attempt(db, user, att.id, answers, now=now + days(0.001))
    db.commit()
    return att


# ---------------------------------------------------------------- V03 eight distinct tasks


def test_v03_eight_tasks_have_their_own_goal_rubric_and_evidence():
    cur = PR.load_curriculum()
    assert list(cur["tasks"]) == ["P1A", "P1B", "P2A", "P2B", "P3A", "P3B", "P4A", "P4B"]
    rubrics = {k: tuple(r["id"] for r in t["rubric"]) for k, t in cur["tasks"].items()}
    assert len(set(rubrics.values())) == 8  # not the same task under eight names
    assert len({t["goal_ja"] for t in cur["tasks"].values()}) == 8
    for t in cur["tasks"].values():
        assert t["stage"] in ("eiken_pre1", "eiken_1", "cefr_c1", "cefr_c2") and t["minutes"][0] < t["minutes"][1]
        assert all(ref.startswith("LQ-REF:") for ref in t["ability_refs"])  # app ids, never called official
    profs = load_profiles()
    practice_profiles = [p for p in profs.values() if p["kind"] == "practice"]
    assert len(practice_profiles) == 4 and not any(p["current"] for p in practice_profiles)
    for mode in ("eiken_pre1", "eiken_1", "cefr_c1", "cefr_c2"):
        assert current_profile(mode)["kind"] != "practice"  # exam / in-app check profiles are untouched
    kinds = {RAW[f]["part"] for f in RAW}
    assert kinds == set(cur["tasks"])  # every task has example material
    for fid in RAW:
        c = content(fid)
        assert c.presentation_profile.kind == "practice" and c.practice.generated
        from lq.generator.authoring import lint_unit

        assert lint_unit(c) == [], fid
    # an item with a rubric id from another task is rejected
    bad = copy.deepcopy(RAW["pr-p1b-bike"])
    bad["questions"][0]["rubric"] = "conclusion"
    with pytest.raises(KeyError):
        expand_fixture(bad)


# ---------------------------------------------------------------- V04 conditions decide the answer; missing info


def test_v04_changing_the_key_condition_changes_the_correct_action():
    a, b = content("pr-p1a-studio-a"), content("pr-p1a-studio-b")
    qa, qb = a.sorted_questions()[0], b.sorted_questions()[0]
    assert qa.prompt_en == qb.prompt_en and qa.answer.choice_id != qb.answer.choice_id  # 2 hours vs 45 minutes
    miss = content("pr-p1a-hall-missing")
    q = miss.sorted_questions()[0]
    right = next(c.text for c in q.choices if c.id == q.answer.choice_id)
    assert "deadline" in right.lower() and "not" in right.lower()  # missing from the audio: "not decided yet" is right


# ---------------------------------------------------------------- V05 critical errors are never offset


def test_v05_critical_misunderstanding_is_reported_even_with_good_details(db, user):
    u = unit(db, "pr-p2a-sleep")
    att = submit(db, user, u, choose(u, {4: crit(u, 4, "certainty")}))  # all right except a certainty mix-up
    res = att.result["practice"]
    assert res["critical_first"] == ["certainty"] and res["first_understanding"] == 0.75
    assert att.result["critical_errors"] == ["certainty"]
    view = L.result_dto(db, att)["practice"]
    assert "確実性" in view["critical_labels"]["certainty"]


# ---------------------------------------------------------------- V06 same content in Japanese / English


def test_v06_short_answers_in_japanese_or_english_are_judged_the_same_and_unclear_is_pending():
    raw = copy.deepcopy(RAW["pr-p3a-festival"])
    raw["questions"][1]["accepted"] = ["opening time", "開店時刻", "開店時間", "what time to open"]
    c = UnitContent.model_validate(expand_fixture(raw))
    q2 = c.sorted_questions()[1]
    base = {q.id: q.answer.model_dump(exclude={"type"}) for q in c.sorted_questions()}
    for text in ["The opening time", "開店時刻はまだ", "they still dont know what time to open"]:
        r = PR.score_practice(c, {**base, q2.id: {"text": text}}, None, [])
        assert r["items"][q2.id]["final"]["correct"], text
    neg = PR.score_practice(c, {**base, q2.id: {"text": "not the opening time"}}, None, [])
    assert not neg["items"][q2.id]["final"]["correct"]  # an added negation is never accepted
    unclear = PR.score_practice(c, {**base, q2.id: {"text": "something about the stall"}}, None, [])
    assert unclear["items"][q2.id]["final"]["pending"] and q2.id in unclear["pending"]
    assert unclear["first_understanding"] == 1.0  # pending is neither right nor wrong


# ---------------------------------------------------------------- V07 checks are recorded apart


def test_v07_a_permitted_check_is_not_first_understanding_and_is_not_a_failure(db, user):
    u = unit(db, "pr-p2b-venue")
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0)
    for seg in UnitContent.model_validate(u.content).segments:
        L.record_play(db, user, att.id, event_id=eid(), segment_id=seg.id)
    first = choose(
        u, {4: crit(u, 4, "certainty")}
    )  # before the check: books Room B without knowing its capacity (certainty error)
    ev = eid()
    rep = L.request_clarification(db, user, att.id, event_id=ev, clarification_id="c1", initial_answers=first)
    assert "thirty" in rep["reply_en"] and rep["used"] == 1
    assert L.request_clarification(db, user, att.id, event_id=ev, clarification_id="c1")["used"] == 1  # re-sent: once
    with pytest.raises(L.Invalid):
        L.request_clarification(db, user, att.id, event_id=eid(), clarification_id="nope")
    att = L.submit_attempt(db, user, att.id, choose(u, {}), now=T0 + days(0.01))
    db.commit()
    res = att.result["practice"]
    assert res["clarifications"] == ["c1"] and res["task_success"] == 1.0
    assert res["first_understanding"] < 1.0 and res["critical_first"] == ["certainty"] and res["critical_final"] == []
    assert att.result["assisted"] is False  # a permitted check is not a transcript / hint


# ---------------------------------------------------------------- V08 / V09 evidence and separation


def test_v08_v09_practice_results_stay_out_of_exam_rates_bands_and_skill_evidence(db, user):
    u = unit(db, "pr-p1a-studio-a")
    att = submit(db, user, u, choose(u, {}))
    assert att.presentation == "practice" and att.result["practice"]["evidence_eligible"] is True
    assert db.execute(select(func.count()).select_from(m.SkillObservation)).scalar_one() == 0
    assert att.result["band"] is None
    # the same audio again never makes new evidence
    again = submit(db, user, u, choose(u, {}), now=T0 + days(1))
    assert (
        again.result["practice"]["evidence_eligible"] is False
        and "既出・同じ音声" in again.result["practice"]["not_evidence_reasons"]
    )
    # a family already met is not offered as "unseen"
    assert practice.unseen_unit(db, user, "P1A") is None
    unit(db, "pr-p1a-studio-b")
    assert practice.unseen_unit(db, user, "P1A") is not None


def test_v15_tone_tasks_without_confirmed_audio_are_not_evidence(db, user):
    u = unit(db, "pr-p4b-report")
    att = submit(db, user, u, choose(u, {}))
    res = att.result["practice"]
    assert res["evidence_eligible"] is False and "語調が実音声で未確認" in res["not_evidence_reasons"]
    assert practice.can_do(db, user) == {}  # nothing claimed from it


# ---------------------------------------------------------------- G01 unlock, never relock


def test_g01_evidence_opens_the_next_task_and_it_is_never_locked_again(db, user, monkeypatch):
    cfg = copy.deepcopy(P.load_progression())
    cfg["unlock"].update(min_tasks=2, min_days=2, min_scenes=2)
    monkeypatch.setattr(P, "load_progression", lambda: cfg)
    a, b = unit(db, "pr-p1a-studio-a"), unit(db, "pr-p1a-studio-b")
    submit(db, user, a, choose(a, {}), now=T0)
    assert practice._states(db, user).get("P2B") is None
    submit(db, user, b, choose(b, {}), now=T0 + days(1))
    st = practice._states(db, user)["P2B"]
    assert st.status == "unlocked" and st.unlock_reason == "recommended" and st.rule_version == cfg["version"]
    # wrong answers afterwards: still unlocked
    c = unit(db, "pr-p1a-hall-missing")
    submit(db, user, c, choose(c, {1: 1, 2: 2, 3: 1}), now=T0 + days(2))
    assert practice._states(db, user)["P2B"].status == "unlocked"


# ---------------------------------------------------------------- V10 / V11 / U02 daily plan


def test_v10_v11_manual_start_with_no_history_joins_today_without_growing_the_day(db, user):
    L.update_user_settings(user, {"check_interval_days": 0})  # keep this day free of the L04 check
    for i in range(4):
        ready_unit(db, content=make_content(part="1A", seed=f"m{i}"))
    unit(db, "pr-p3a-festival")
    plan = plans.start_plan(db, user, T0)
    before = (plan.budget_sec, plan.planned_sec)
    assert not db.execute(
        select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id, m.PlanSlot.kind == "practice")
    ).first()
    out = practice.manual_start(db, user, "P3A", T0)
    db.commit()
    assert out["added_today"] is True
    slots = db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id)).scalars().all()
    ps = [s for s in slots if s.kind == "practice"]
    assert len(ps) == 1 and ps[0].reason_params["practice_kind"] == "P3A" and ps[0].mode == "cefr_c1"
    assert any(s.state == "removed" and "置き換え" in (s.carry_reason or "") for s in slots)
    assert plan.budget_sec == before[0] and plan.planned_sec <= plan.budget_sec
    st = practice._states(db, user)["P3A"]
    assert st.unlock_reason == "manual" and st.evidence == []  # no evidence, mastery or XP from the wish alone
    row = next(r for r in practice.overview(db, user, T0)["tasks"] if r["kind"] == "P3A")
    assert row["status"] == "unlocked" and row["reason_ja"] == "本人の希望で開始"


def test_u02_the_main_goals_own_task_enters_the_plan_and_common_test_focus_gets_none(db, user):
    for i in range(4):
        ready_unit(db, content=make_content(mode="eiken_pre1", part="part1", seed=f"p{i}"))
        ready_unit(db, content=make_content(part="1A", seed=f"c{i}"))
    unit(db, "pr-p1a-studio-a")
    plan = plans.start_plan(db, user, T0)  # common_test focus: no upper-stage task pushed in
    assert not [
        s for s in db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id)).scalars() if s.kind == "practice"
    ]
    goals.decide(db, user, eid(), "manual_switch", "eiken_pre1", T0 + days(1))
    db.commit()
    plan2 = plans.start_plan(db, user, T0 + days(1))
    ps = [
        s
        for s in db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan2.id)).scalars()
        if s.kind == "practice"
    ]
    assert len(ps) == 1 and ps[0].reason_text.startswith("実践: 条件に合う行動を選ぶ")
    # through the plan: answer, then the slot completes with the explanation step
    att = submit(
        db,
        user,
        db.get(m.Unit, ps[0].unit_id),
        choose(db.get(m.Unit, ps[0].unit_id), {}),
        now=T0 + days(1),
        slot_id=ps[0].id,
    )
    L.ack_feedback(db, user, att.id, now=T0 + days(1.01))
    db.commit()
    db.refresh(ps[0])
    assert ps[0].state == "completed"
    done = practice.can_do(db, user)["P1A"]
    assert {d["label_ja"] for d in done} >= {"行動の妥当性", "決め手の条件"}


def test_short_day_gets_no_practice_task(db, user):
    L.update_user_settings(user, {"active_mode": "eiken_pre1", "daily_goal_minutes": 5})
    ready_unit(db, content=make_content(mode="eiken_pre1", part="part1", seed="s"))
    unit(db, "pr-p1a-studio-a")
    db.commit()
    plan = plans.start_plan(db, user, T0)
    assert not [
        s for s in db.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == plan.id)).scalars() if s.kind == "practice"
    ]


def test_api_practice_overview_and_clarify(db, user):
    from test_api import login

    u = unit(db, "pr-p1a-hall-missing")
    db.commit()
    c = login()
    ov = c.get("/api/practice").json()
    assert len(ov["tasks"]) == 8 and "認定ではありません" in ov["note_ja"]
    p4b = next(t for t in ov["tasks"] if t["kind"] == "P4B")
    assert p4b["material_ja"] and "能力不足ではありません" in p4b["material_ja"]
    aid = eid()
    c.post("/api/attempts", json={"attempt_id": aid, "unit_id": u.id})
    view = c.get(f"/api/attempts/{aid}").json()
    assert view["unit"]["practice"]["clarifications"][0]["label_ja"] and "reply_en" not in str(view["unit"]["practice"])
    r = c.post(
        f"/api/attempts/{aid}/clarify", json={"event_id": eid(), "clarification_id": "c1", "initial_answers": {}}
    ).json()
    assert "three working days" in r["reply_en"]
    assert c.post("/api/practice/P2A/start").json()["kind"] == "P2A"


def test_m01_growth_keeps_practice_apart_from_exam_figures(db, user):
    from lq.services import stats

    u = unit(db, "pr-p1a-studio-a")
    submit(db, user, u, choose(u, {1: crit(u, 1, "condition")}))  # online booking for two hours: a condition mix-up
    g = stats.growth(db, user, T0 + days(1), "eiken_pre1")
    assert g["first_official"]["units"] == 0 and g["first_arranged"]["units"] == 0 and g["review"]["units"] == 0
    assert g["practice"]["tasks"] == 1 and g["practice"]["critical"] == 1 and g["practice"]["first_mean"] == 0.5


# ---------------------------------------------------------------- 選択肢の位置と手掛かり (option order and cues)


def test_bundled_tasks_have_no_position_or_wording_cues():
    from lq.domain.cue_audit import audit_units

    r = audit_units([content(f) for f in RAW])
    assert r["questions"] == 32 and r["max_position_share"] <= 0.3, r["answer_positions"]
    assert r["flagged"] == []


def test_choices_are_shown_in_a_fixed_per_attempt_order_and_graded_by_id(db, user):
    from lq.services import learning as L2

    orders, u = set(), unit(db, "pr-p3a-festival")
    db.commit()
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=u.id, now=T0)
    db.commit()
    first = L2.attempt_dto(db, att)["unit"]["questions"]
    again = L2.attempt_dto(db, db.get(m.Attempt, att.id))["unit"]["questions"]  # reload / resume
    assert [[c["id"] for c in q["choices"]] for q in first] == [[c["id"] for c in q["choices"]] for q in again]
    for i in range(6):  # other attempts get other orders
        orders.add(
            tuple(
                c["id"]
                for c in L2.attempt_dto(db, m.Attempt(id=f"x{i}" * 8, unit_id=u.id, plays={}, aids={}))["unit"][
                    "questions"
                ][0]["choices"]
            )
        )
    assert len(orders) > 1
    c = UnitContent.model_validate(u.content)
    shown = {q["id"]: [ch["id"] for ch in q["choices"]] for q in first}
    authored = {q.id: [ch.id for ch in q.choices] for q in c.questions}
    assert any(shown[q] != authored[q] for q in shown)  # the shown order differs from the authored one
    for seg in c.segments:
        L.record_play(db, user, att.id, event_id=eid(), segment_id=seg.id)
    att = L.submit_attempt(db, user, att.id, choose(u, {}), now=T0 + days(0.01))  # answers are choice ids
    db.commit()
    assert att.result["correct_count"] == att.result["total"]
    res = L.result_dto(db, att)
    assert [[ch["id"] for ch in q["choices"]] for q in res["questions"]] == [shown[q["id"]] for q in res["questions"]]
    for q in res["questions"]:  # each wrong-choice reason stays on its own option
        assert set(q["distractors"]) == {ch["id"] for ch in q["choices"]} - {q["correct_answer"]["choice_id"]}


def test_practice_answer_audio_names_no_option_number():
    from lq.audio import render as R

    clips = R.plan_clips(content("pr-p3a-festival"))
    assert all("number" not in s.text for k, s in clips.items() if k.startswith("answer:"))


def test_generated_exam_items_spread_the_answer_position():
    from collections import Counter

    from helpers import make_content

    from lq.generator import authoring
    from lq.generator.providers import GenSpec

    pos = Counter()
    for i in range(24):
        c = make_content(part="3", seed=f"spread{i}")
        raw = {"title": c.title, "topic": c.topic, "family_slug": "f", "pre_info_situation": "", "figures": [], "radio_figure_description_en": "", "glossary": [], "translations_ja": [],
               "speakers": [s.model_dump() for s in c.speakers if s.role != "narrator"],
               "segments": [{"id": s.id, "label": s.label, "utterances": [{"id": x.id, "speaker_id": x.speaker_id, "text": x.text} for x in s.utterances]} for s in c.segments],
               "questions": [{"id": q.id, "slot": q.slot, "segment_ids": q.segment_ids, "prompt_en": q.prompt_en, "type": "single_choice",
                              "choices": [ch.model_dump() for ch in q.choices], "items": [], "figure_ids": [], "answer_choice_ids": [q.choices[0].id], "answer_pairs": [],
                              "answer_texts": [], "skill_tags": q.skill_tags, "evidence": [{"utterance_id": e.utterance_id or "", "figure_id": "", "element_id": ""} for e in q.evidence],
                              "answer_en": "x", "explanation_ja": "説明。", "hint_ja": "", "distractors": []} for q in c.questions]}  # fmt: skip
        u = authoring.authored_to_unit(
            raw,
            GenSpec(
                mode="common_test",
                part="3",
                template_type="short_dialogue",
                primary_skill="explicit_detail",
                band=3,
                seed=f"spread{i}",
            ),
        )
        for q in u["questions"]:
            pos[[ch["id"] for ch in q["choices"]].index(q["answer"]["choice_id"]) + 1] += 1
    assert len(pos) == 4 and max(pos.values()) / sum(pos.values()) < 0.5, pos  # the model put every answer first

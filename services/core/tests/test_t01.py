"""統合指示書 T01 / Q01: practice tasks are generated from their versioned definition, behind the usual cost gates."""

from helpers import T0, eid
from test_pipeline import enqueue, unit_of
from test_practice import content

from lq.domain.content import UnitContent
from lq.generator import authoring
from lq.generator.authoring import lint_unit
from lq.generator.providers import GenSpec
from lq.jobs import worker
from lq.services import goals, practice, replenish

SPEC = dict(
    mode="eiken_1",
    part="P2B",
    template_type="practice_task",
    primary_skill="condition_matching",
    band=3,
    seed="t01",
    practice_kind="P2B",
)


def _authored_from(c: UnitContent) -> dict:
    """What a content model would return for this task (the authored schema)."""
    return {
        "title": c.title, "topic": c.topic, "family_slug": "venue-update", "pre_info_situation": "", "figures": [], "radio_figure_description_en": "",
        "speakers": [s.model_dump() for s in c.speakers if s.role != "narrator"],
        "segments": [{"id": s.id, "label": s.label, "utterances": [{"id": u.id, "speaker_id": u.speaker_id, "text": u.text} for u in s.utterances]} for s in c.segments],
        "questions": [{"id": q.id, "slot": q.slot, "segment_ids": q.segment_ids, "prompt_en": q.prompt_en, "type": "single_choice", "choices": [ch.model_dump() for ch in q.choices],
                       "items": [], "figure_ids": [], "answer_choice_ids": [q.answer.choice_id], "answer_pairs": [], "answer_texts": [], "skill_tags": q.skill_tags,
                       "evidence": [{"utterance_id": e.utterance_id or "", "figure_id": "", "element_id": ""} for e in q.evidence], "answer_en": q.feedback.answer_en,
                       "explanation_ja": q.feedback.explanation_ja, "hint_ja": "", "distractors": [d.model_dump() for d in q.feedback.distractors]} for q in c.questions],
        "glossary": [], "translations_ja": [],
        "purpose_ja": c.practice.purpose_ja, "listener_ja": "", "success_ja": c.practice.success_ja, "scene": "update-2",
        "rubrics": [{"question_id": it.question_id, "rubric": it.rubric, "critical": [{"choice_id": k, "kind": v} for k, v in it.critical_choices.items()],
                     "accept_choice_ids": it.accept_choice_ids} for it in c.practice.items],
        "clarifications": [c2.model_dump() for c2 in c.practice.clarifications],
        "audio_dependency_en": "Room B's new capacity decides whether it can be booked.",
    }  # fmt: skip


def test_prompt_is_built_from_the_task_definition_and_the_schema_is_strict():
    system, user = authoring.build_generation_prompt(GenSpec(**SPEC))
    for s in (
        "Practice task P2B",
        "provisional",
        "need to check",
        "critical misunderstandings",
        "NOT an official exam item",
        "condition",
    ):
        assert s in user, s
    schema = authoring.authored_schema(True)
    assert {"rubrics", "clarifications", "purpose_ja", "audio_dependency_en"} <= set(schema["required"])
    assert "rubrics" not in authoring.authored_schema(False)["properties"]  # exam generation unchanged


def test_model_output_becomes_valid_practice_material():
    raw = _authored_from(content("pr-p2b-venue"))
    data = authoring.authored_to_unit(raw, GenSpec(**SPEC))
    u = UnitContent.model_validate(data)
    assert lint_unit(u) == [] and u.presentation_profile.kind == "practice" and u.radio.supported is False
    assert u.practice.practice_kind == "P2B" and u.practice.max_clarifications == 1 and u.practice.audio_needed_checked


def test_demo_generation_job_reaches_ready_and_is_offered_as_unseen(db, user):
    jid = enqueue(db, **SPEC)
    assert worker.drain("w") == ["succeeded"]
    job, unit = unit_of(db, jid)
    assert unit.status == "READY" and unit.template_type == "practice_task" and unit.part == "P2B"
    assert practice.unseen_unit(db, user, "P2B").id == unit.id


def test_restock_only_for_open_kinds_and_never_under_common_test_focus(db, user):
    assert replenish.practice_jobs(db, user) == []  # common test focus, nothing opened
    goals.decide(db, user, eid(), "manual_switch", "eiken_pre1", T0)
    db.commit()
    specs = replenish.practice_jobs(db, user)
    assert {s["practice_kind"] for s in specs} == {"P1A", "P1B"} and all(s["mode"] == "eiken_pre1" for s in specs)


def test_admin_can_ask_for_a_practice_task_of_its_own_stage_only(db, user):
    from test_api import login

    c = login()
    ok = c.post(
        "/api/admin/jobs", json={"mode": "eiken_pre1", "part": "P1A", "primary_skill": "condition_matching", "band": 3}
    )
    assert ok.status_code == 200 and len(ok.json()["job_ids"]) == 1
    bad = c.post(
        "/api/admin/jobs", json={"mode": "cefr_c1", "part": "P1A", "primary_skill": "condition_matching", "band": 3}
    )
    assert bad.status_code == 422

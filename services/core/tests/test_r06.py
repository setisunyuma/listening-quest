"""R06: the exam format is a checkable contract in the profile, checked for free before paid review / audio."""

from helpers import make_content, ready_unit
from test_api import _reserve_full_set, login

from lq.db import models as m
from lq.domain.profiles import current_profile, get_profile, part_spec
from lq.generator.authoring import contract_errors, lint_unit
from lq.services import assessment
from lq.services.seed import load_fixture_units


def _drop_choice(u, keep):
    qs = []
    for q in u.questions:
        kept = q.choices[:keep]
        if q.answer.choice_id not in {c.id for c in kept}:
            kept = [*q.choices[: keep - 1], next(c for c in q.choices if c.id == q.answer.choice_id)]
        ids = {c.id for c in kept}
        fb = q.feedback.model_copy(update={"distractors": [d for d in q.feedback.distractors if d.choice_id in ids]})
        qs.append(q.model_copy(update={"choices": kept, "feedback": fb}))
    return u.model_copy(update={"questions": qs})


def test_four_choice_parts_pass_and_a_two_choice_unit_fails():
    for mode, part in (("common_test", "1A"), ("eiken_pre1", "part1"), ("eiken_1", "part1")):
        u = make_content(mode=mode, part=part, seed=f"r06-{mode}")
        assert lint_unit(u) == [], (mode, lint_unit(u))
        assert all(len(q.choices) == 4 for q in u.questions)
    u = make_content(part="1A", seed="r06-two")
    errs = contract_errors(_drop_choice(u, 2), part_spec(current_profile("common_test"), "1A"))
    assert any("needs 4 choices, got 2" in e for e in errs)


def test_4a_order_needs_five_shared_candidates_for_four_answers():
    part = part_spec(current_profile("common_test"), "4A_order")
    u = make_content(part="4A_order", skill="discourse_structure", seed="r06-4a")
    qs = u.sorted_questions()
    assert len(qs) == 4 and all(len(q.choices) == 5 for q in qs) and contract_errors(u, part) == []
    assert {q.answer.choice_id for q in qs} <= {c.id for c in qs[0].choices}
    four = _drop_choice(u, 4)
    assert any("needs 5 choices" in e for e in contract_errors(four, part))
    # choices that are not shared across the four answers are rejected
    q2 = qs[1]
    other = q2.model_copy(update={"choices": [c.model_copy(update={"text": c.text + " later"}) for c in q2.choices]})
    mixed = u.model_copy(update={"questions": [other if q.id == q2.id else q for q in u.questions]})
    assert any("share the same choices" in e for e in contract_errors(mixed, part))


def test_part5_slot6_compares_both_students_and_slot7_needs_lecture_conversation_figure():
    part = part_spec(current_profile("common_test"), "5")
    u = make_content(part="5", skill="integration", seed="r06-5")
    assert contract_errors(u, part) == [] and [s.label for s in u.segments] == ["lecture", "comments", "conversation"]
    assert len(u.questions) == 7  # seven answer slots kept
    seg_of = {ut.id: s.label for s in u.segments for ut in s.utterances}
    q6 = next(q for q in u.questions if q.slot == 6)
    q7 = next(q for q in u.questions if q.slot == 7)
    # slot 6 with only one student's comment
    comments = [e for e in q6.evidence if e.utterance_id and seg_of[e.utterance_id] == "comments"]
    spk = {ut.id: ut.speaker_id for s in u.segments for ut in s.utterances}
    first = spk[comments[0].utterance_id]
    one = [
        e
        for e in q6.evidence
        if not (e.utterance_id and seg_of.get(e.utterance_id) == "comments") or spk[e.utterance_id] == first
    ]
    bad6 = u.model_copy(
        update={"questions": [q.model_copy(update={"evidence": one}) if q.slot == 6 else q for q in u.questions]}
    )
    assert any("slot 6" in e and "both students" in e for e in contract_errors(bad6, part))
    # slot 7 that uses the comments instead of the conversation, and no figure
    ev7 = [e for e in q7.evidence if e.utterance_id and seg_of[e.utterance_id] == "lecture"] + comments
    bad7 = u.model_copy(
        update={"questions": [q.model_copy(update={"evidence": ev7}) if q.slot == 7 else q for q in u.questions]}
    )
    errs = contract_errors(bad7, part)
    assert any("slot 7" in e and "conversation" in e for e in errs) and any(
        "slot 7" in e and "figure" in e for e in errs
    )
    # a missing worksheet figure
    nofig = u.model_copy(update={"figures": [f for f in u.figures if f.kind != "worksheet"]})
    assert any("worksheet figure" in e for e in contract_errors(nofig, part))
    # unlabelled segments
    plain = u.model_copy(update={"segments": [s.model_copy(update={"label": ""}) for s in u.segments]})
    assert any("segments must be labelled" in e for e in contract_errors(plain, part))


def test_picture_parts_described_in_words_are_arranged_never_official():
    part = part_spec(current_profile("common_test"), "1B")
    u = make_content(part="1B", seed="r06-1b")
    assert u.presentation_profile.kind == "audio_arranged" and contract_errors(u, part) == []
    official = u.model_copy(
        update={"presentation_profile": u.presentation_profile.model_copy(update={"kind": "official"})}
    )
    assert any("audio_arranged" in e for e in contract_errors(official, part))


def test_fixtures_conform_to_the_current_contract():
    units = {u.content_id: u for u in load_fixture_units()}
    assert [s.label for s in units["ct-5-naps-lecture"].segments] == ["lecture", "comments", "conversation"]
    assert all(len(q.choices) == 5 for q in units["ct-4a-order-saturday"].questions)


def test_old_profile_version_is_rechecked_for_free_and_not_overwritten(db, user):
    old = get_profile("common_test/2026_main")
    assert old.get("current") is False and current_profile("common_test")["id"] == "common_test/2026_main_r2"
    cur = current_profile("common_test")
    ok = make_content(part="3", seed="r06-old-ok").model_copy(update={"profile_version": old["id"]})
    assert lint_unit(ok) == []  # still valid under its own version (history kept)
    u_ok = ready_unit(db, content=ok, pool="assessment")
    bad = _drop_choice(make_content(part="3", seed="r06-old-bad"), 2).model_copy(update={"profile_version": old["id"]})
    u_bad = ready_unit(db, content=bad, pool="assessment")
    assert assessment.fits_profile(db, u_ok, cur) is True
    assert assessment.fits_profile(db, u_bad, cur) is False
    assert db.get(m.Unit, u_bad.id).profile_version == old["id"]  # not rewritten


def test_a_set_with_word_described_pictures_is_not_a_standard_result(db, user):
    _reserve_full_set(db, "common_test")
    ms = assessment.assemble(db, "demo", "common_test")
    db.commit()
    assert ms.validation["arranged_parts"] == ["1B", "2"] and "contract" in ms.validation["checked"]
    c = login()
    sid = c.post("/api/assessments", json={"mock_set_id": ms.id}).json()["session_id"]
    res = c.post(f"/api/assessments/{sid}/submit", json={"answers": {}}).json()
    assert res["standard"] is False and "arranged_format" in res["conditions"] and res["arranged_parts"] == ["1B", "2"]


def test_drawn_pictures_make_1b_and_part2_official_and_wrong_panel_counts_fail():
    from pathlib import Path

    import yaml as _y

    from lq.domain.content import UnitContent
    from lq.services.seed import expand_fixture

    raws = {
        r["id"]: r
        for r in _y.safe_load(
            (Path(__file__).resolve().parents[3] / "fixtures/units/common_test.yaml").read_text(encoding="utf-8")
        )
    }
    for fid, part in (("ct-1b-pic-reading", "1B"), ("ct-2-pic-meeting-time", "2")):
        u = UnitContent.model_validate(expand_fixture(raws[fid]))
        assert u.presentation_profile.kind == "official" and lint_unit(u) == []
        f = u.figures[0]
        three = u.model_copy(update={"figures": [f.model_copy(update={"pictures": f.pictures[:3]})]})
        assert any(
            "audio_arranged" in e for e in contract_errors(three, part_spec(current_profile("common_test"), part))
        )
        bad = u.model_copy(update={"questions": [q.model_copy(update={"figure_ids": []}) for q in u.questions]})
        assert any("audio_arranged" in e for e in contract_errors(bad, part_spec(current_profile("common_test"), part)))


def test_mock_result_names_the_arranged_questions_in_japanese(db, user):
    _reserve_full_set(db, "common_test")
    ms = assessment.assemble(db, "demo", "common_test")
    db.commit()
    c = login()
    sid = c.post("/api/assessments", json={"mock_set_id": ms.id}).json()["session_id"]
    res = c.post(f"/api/assessments/{sid}/submit", json={"answers": {}}).json()
    labels = [d["label_ja"] for d in res["arranged_detail"]]
    assert labels == ["第1問B 場面と絵の対応（問5〜8）", "第2問 短い対話と問い（問9〜11）"]
    assert "絵を見比べて" in res["arranged_detail"][0]["note_ja"]

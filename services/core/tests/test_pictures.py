"""Picture variety: attributes, a numbered scene (Part 2), the difference axis rotation and similar-composition check.
Checked against the 2024 / 2025 Common Test pictures (shapes x shades, worn things, counts, states, numbered scenes)."""

from pathlib import Path

import yaml
from helpers import eid

from lq.db import models as m
from lq.domain import pictures as P
from lq.domain.content import UnitContent
from lq.domain.profiles import current_profile, part_spec
from lq.generator.authoring import (
    AFigure,
    _figures,
    authored_schema,
    build_generation_prompt,
    contract_errors,
    lint_unit,
)
from lq.generator.providers import GenSpec
from lq.jobs import pipeline
from lq.services import content as C
from lq.services.seed import expand_fixture

RAWS = {
    r["id"]: r
    for r in yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "fixtures/units/common_test.yaml").read_text("utf-8")
    )
}


def unit_of(fid: str, **over) -> UnitContent:
    return UnitContent.model_validate({**expand_fixture(RAWS[fid]), **over})


def test_hand_made_picture_items_are_official_and_differ_in_the_intended_way():
    want = {
        "ct-1b-pic-watch": ["shade", "shape"],
        "ct-1b-pic-scarves": ["wear"],
        "ct-2-scene-envelope": ["position", "shade"],
        "ct-2-pic-meeting-time": ["time"],
    }
    for fid, axes in want.items():
        u = unit_of(fid)
        assert u.presentation_profile.kind == "official" and lint_unit(u) == [], (fid, lint_unit(u))
        assert P.contrast_axes(P.picture_figure(u.figures)) == axes, fid
    assert {"count", "state"} <= set(P.contrast_axes(P.picture_figure(unit_of("ct-2-pic-fish-count").figures)))
    assert "state" in P.contrast_axes(P.picture_figure(unit_of("ct-1b-pic-tree-season").figures))


def test_labeled_scene_needs_marks_1_to_4_and_is_allowed_only_where_the_exam_uses_it():
    u = unit_of("ct-2-scene-envelope")
    part2 = part_spec(current_profile("common_test"), "2")
    assert contract_errors(u, part2) == []
    f = u.figures[0]
    items = f.pictures[0].items
    no4 = [it.model_copy(update={"mark": 0}) if it.mark == 4 else it for it in items]
    bad = u.model_copy(
        update={"figures": [f.model_copy(update={"pictures": [f.pictures[0].model_copy(update={"items": no4})]})]}
    )
    assert any("labeled_scene" in e for e in contract_errors(bad, part2))
    two3 = [it.model_copy(update={"mark": 3}) if it.mark == 4 else it for it in items]
    bad2 = u.model_copy(
        update={"figures": [f.model_copy(update={"pictures": [f.pictures[0].model_copy(update={"items": two3})]})]}
    )
    assert contract_errors(bad2, part2) != []
    # 1B always shows four separate pictures in the exam: a numbered scene there is not the official format
    as_1b = u.model_copy(
        update={"part": "1B", "template_type": part_spec(current_profile("common_test"), "1B")["template_type"]}
    )
    assert any(
        "allowed picture forms" in e for e in contract_errors(as_1b, part_spec(current_profile("common_test"), "1B"))
    )


def test_schema_describes_the_attributes_and_generated_values_are_normalised():
    props = authored_schema()["$defs"]["APicItem"]["properties"]
    for k in ("shade", "pattern", "shape", "count", "state", "hair", "wear", "mark"):
        assert k in props and props[k].get("description"), k
    base = {"kind": "fish", "x": 50, "y": 50, "pose": "", "label": "", "flip": False, "size": 100, "shade": "",
            "pattern": "", "shape": "", "count": 40, "state": "", "hair": "", "wear": [], "mark": 3}  # fmt: skip
    fig = {"id": "f1", "kind": "picture_set", "title": "", "columns": [], "rows": [], "note": "",
           "pictures": [{"id": "p1", "items": [base]}]}  # fmt: skip
    it = _figures([AFigure.model_validate(fig)])[0]["pictures"][0]["items"][0]
    assert it["count"] == 12 and it["mark"] == 0  # marks only belong to a labeled scene


def _register(db, fid: str, cid: str) -> m.Unit:
    u = unit_of(fid, content_id=cid)
    unit, _ = C.register_unit(db, "demo", u, pool="practice")
    db.commit()
    return unit


def test_generation_rotates_the_difference_and_a_repeated_composition_is_caught(db, user):
    spec = GenSpec(mode="common_test", part="1B", template_type="scene_match", primary_skill="explicit_detail", band=3,
                   seed="rot-1")  # fmt: skip
    # nothing in stock: some axis is chosen, and the prompt asks for it
    first = pipeline.picture_contrast_for(db, "demo", spec)
    assert first in P.AXES
    _, user_prompt = build_generation_prompt(GenSpec(**{**spec.__dict__, "extra": {"picture_contrast": "pattern"}}))
    assert "differ mainly in 'pattern'" in user_prompt
    # with watches (shade, shape) and scarves (wear) in stock those axes are no longer the least used
    _register(db, "ct-1b-pic-watch", "ct-1b-watch-a")
    _register(db, "ct-1b-pic-scarves", "ct-1b-scarves-a")
    assert pipeline.picture_contrast_for(db, "demo", spec) not in ("shade", "shape", "wear")
    # exam parts without pictures get no axis
    assert pipeline.picture_contrast_for(db, "demo", GenSpec(mode="common_test", part="3", template_type="short_dialogue",
                                                             primary_skill="inference", band=3)) is None  # fmt: skip
    # the same kinds of things differing in the same way: caught before review / audio are paid for
    again = _register(db, "ct-1b-pic-watch", "ct-1b-watch-b")
    reason = pipeline.similar_picture_item(db, again, C.load_content(again))
    assert reason and "ct-1b-watch-a" in reason and "似た構図" in reason
    other = _register(db, "ct-1b-pic-tree-season", "ct-1b-tree-a")
    assert pipeline.similar_picture_item(db, other, C.load_content(other)) is None
    assert eid()  # helpers imported for the shared fixtures

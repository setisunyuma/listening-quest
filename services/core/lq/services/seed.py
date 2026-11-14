"""Seeding: mode profiles, the hand-written example units, and the demo data area.

Example units are checked by their author (script, key, evidence, distractor reasons) and recorded as
`author_reviewed` - never as an API or independent human review. In the live area they still need real
audio (API synthesis or imported audio that passes checks) before they become READY. The demo area gets
tone audio ("音声動作確認用") and mock checks so the whole flow can be exercised without paid APIs.
Everything here is idempotent."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

import yaml
from sqlalchemy import select

from lq.config import get_settings
from lq.db import models as m
from lq.domain.content import UnitContent
from lq.domain.profiles import current_profile, load_profiles, part_spec, profile_hash
from lq.generator import budget
from lq.generator.authoring import lint_unit, unit_scoring_groups
from lq.jobs import queue
from lq.services import auth
from lq.services import content as C


def seed_profiles(session) -> int:
    n = 0
    for pid, prof in load_profiles().items():
        row = session.get(m.ModeProfile, pid)
        h = profile_hash(prof)
        if row is None:
            session.add(m.ModeProfile(id=pid, mode=prof["mode"], version=prof["version"], spec=prof, spec_hash=h))
            n += 1
        elif row.spec_hash != h:
            row.spec, row.spec_hash = prof, h
            n += 1
    return n


def _sid(label: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", label.lower())[:20]


def expand_fixture(raw: dict) -> dict:
    """Expand the compact authoring notation of fixtures/units/*.yaml into a schema-2 unit dict."""
    profile = current_profile(raw["mode"])
    part = part_spec(profile, raw["part"])
    speakers = [{"id": "narrator", "role": "narrator", "label": "Narrator", "voice_hint": "neutral"}]
    speakers += [
        {"id": _sid(lbl), "role": "speaker", "label": lbl, "voice_hint": hint} for lbl, hint in raw["speakers"].items()
    ]
    segments, n = [], 0
    for si, seg in enumerate(raw["script"], 1):
        utts = []
        for line in seg:
            lbl, text = line.split(": ", 1)
            n += 1
            utts.append({"id": f"u{n}", "speaker_id": _sid(lbl), "lang": "en", "text": text})
        segments.append({"id": f"s{si}", "label": "", "plays": part["plays"], "utterances": utts})
    roles = (part.get("contract") or {}).get("segment_roles")
    if roles and len(roles) == len(
        segments
    ):  # R06: part 5 segments carry their role (lecture / comments / conversation)
        for seg, role in zip(segments, roles, strict=True):
            seg["label"] = role
    figures = []
    for f in raw.get("figures", []):
        rows = [
            {"id": f"r{ri}", "cells": [{"id": f"r{ri}c{ci}", "text": str(c)} for ci, c in enumerate(row, 1)]}
            for ri, row in enumerate(f["rows"], 1)
        ]
        figures.append(
            {
                "id": f["id"],
                "kind": f["kind"],
                "title": f.get("title", ""),
                "columns": f.get("columns", []),
                "rows": rows,
                "note": f.get("note", ""),
            }
        )
    questions = []
    for qi, q in enumerate(raw["questions"], 1):
        texts = q.get("choices") or raw["common_choices"]
        choices = [{"id": f"c{i}", "text": str(t)} for i, t in enumerate(texts, 1)]
        wrong_ids = [c["id"] for c in choices if c["id"] != f"c{q['answer']}"]
        if len(q["wrong"]) != len(wrong_ids):
            raise ValueError(f"{raw['id']} q{qi}: needs one reason per wrong choice")
        evidence = []
        for e in q["evidence"]:
            if isinstance(e, int):
                evidence.append({"utterance_id": f"u{e}"})
            else:
                fid, el = str(e).split(":")
                evidence.append({"figure_id": fid, "element_id": el})
        skills = q.get("skills") or [q["skill"]]
        questions.append({
            "id": f"q{qi}", "slot": qi, "segment_ids": [f"s{i}" for i in q.get("segments", [])], "prompt_en": q["prompt"], "prompt_medium": part["question_medium"],
            "prompt_timing": part["question_timing"], "type": "single_choice", "choices": choices, "items": [], "figure_ids": q.get("figures", []),
            "answer": {"type": "single_choice", "choice_id": f"c{q['answer']}"}, "skill_tags": skills, "evidence": evidence,
            "feedback": {"answer_en": q["answer_en"], "explanation_ja": q["ja"], "hint_ja": q.get("hint", ""), "distractors": [{"choice_id": cid, "reason_ja": r} for cid, r in zip(wrong_ids, q["wrong"], strict=True)]},
        })  # fmt: skip
    skills = sorted({s for q in questions for s in q["skill_tags"]})
    primary = raw.get("primary_skill") or max(skills, key=lambda s: sum(1 for q in questions if s in q["skill_tags"]))
    return {
        "schema_version": 2, "source": "fixture", "content_id": raw["id"], "revision": raw.get("revision", 1), "mode": raw["mode"], "profile_version": profile["id"],
        "part": raw["part"], "template_type": part["template_type"], "title": raw["title"], "topic": raw["topic"], "skill_tags": skills, "primary_skill": primary,
        "provisional_difficulty_band": raw["band"], "calibration_status": "provisional", "family_id": raw["family"], "variant_of": raw.get("variant_of"),
        "presentation_profile": _presentation(part), "speakers": speakers,
        "pre_info": {"situation_en": raw["pre"], "read_seconds": part.get("pre_read_sec", 10)} if raw.get("pre") else None, "figures": figures, "segments": segments,
        "questions": questions, "scoring_groups": unit_scoring_groups(profile, raw["part"], [q["id"] for q in questions]),
        "radio": {"supported": raw.get("radio", True), "reason": raw.get("radio_reason", ""), "figure_description_en": raw.get("radio_desc", "")},
    }  # fmt: skip


def _presentation(part: dict) -> dict:
    """R06: parts that choose between pictures are described in words in this app - an arranged presentation."""
    if (part.get("contract") or {}).get("requires_pictures"):
        return {
            "kind": "audio_arranged",
            "choices_read_aloud": False,
            "figure_verbalized": True,
            "notes": "pictures described in words (形式調整版)",
        }
    return {"kind": "official", "choices_read_aloud": False, "figure_verbalized": False, "notes": ""}


def load_fixture_units(directory: Path | None = None) -> list[UnitContent]:
    directory = directory or get_settings().fixtures_dir / "units"
    out = []
    for p in sorted(directory.glob("*.yaml")):
        for raw in yaml.safe_load(p.read_text(encoding="utf-8")):
            unit = UnitContent.model_validate(expand_fixture(raw))
            errs = lint_unit(unit)
            if errs:
                raise ValueError(f"fixture {raw['id']}: {errs}")
            out.append(unit)
    return out


def seed_fixtures(session, realm: str) -> dict:
    """Register the example units (idempotent) with an author_reviewed record and queue their audio."""
    created = dup = 0
    for content in load_fixture_units():
        unit, status = C.register_unit(session, realm, content)
        if status == "created":
            created += 1
        else:
            dup += 1
        if C.passing_review(session, unit, "content") is None:
            C.add_review_record(session, unit, target="content", method="author_reviewed", reviewer="fixture-author",
                                result="pass", details={"note": "script, key, evidence and distractor reasons checked by the author of the bundled examples"})  # fmt: skip
        if unit.status not in ("READY", "QUARANTINED"):
            queue.enqueue(
                session,
                realm,
                "process_unit",
                {"origin": "fixture", "mode": content.mode, "part": content.part},
                dedupe_key=f"process:{unit.id}",
                unit_id=unit.id,
            )
    return {"created": created, "already_present": dup}


DEMO_PLAN = {  # extra procedurally generated demo units per mode: (part, skill, band) ...
    "common_test": [("1A", "explicit_detail", 2), ("1A", "numbers_negation", 3), ("1B", "explicit_detail", 3), ("2", "numbers_negation", 3), ("2", "inference", 3), ("3", "speaker_attitude", 3),
                    ("3", "numbers_negation", 3), ("3", "inference", 4), ("3", "main_idea", 3), ("3", "discourse_structure", 3), ("4B", "condition_matching", 3), ("6A", "speaker_attitude", 3),
                    ("6B", "inference", 3), ("3", "numbers_negation", 2), ("2", "explicit_detail", 4), ("1A", "inference", 3)],
    "eiken_pre1": [("part1", "inference", 3), ("part1", "numbers_negation", 3), ("part1", "speaker_attitude", 3), ("part2", "explicit_detail", 3), ("part2", "main_idea", 3), ("part3", "condition_matching", 3), ("part1", "explicit_detail", 2)],
    "eiken_1": [("part1", "inference", 3), ("part1", "speaker_attitude", 4), ("part2", "explicit_detail", 3), ("part3", "condition_matching", 3), ("part4", "main_idea", 3), ("part1", "numbers_negation", 3)],
    "cefr_c1": [("lecture", "main_idea", 3), ("discussion", "speaker_attitude", 3), ("interview", "inference", 3), ("lecture", "discourse_structure", 3), ("discussion", "numbers_negation", 3)],
    "cefr_c2": [("implicature", "inference", 3), ("attitude", "speaker_attitude", 3), ("discourse_integration", "integration", 3), ("implicature", "numbers_negation", 3), ("attitude", "inference", 3)],
}  # fmt: skip


def seed_demo(
    session,
    *,
    username: str = "demo",
    password: str | None = None,
    with_mock: bool = True,
    mock_modes: tuple[str, ...] = ("common_test", "cefr_c1"),
) -> dict:
    """Create / refresh the demo data area. Never touches the live area."""
    out: dict = {"password_created": None}
    user = session.execute(select(m.User).where(m.User.username == username)).scalar_one_or_none()
    if user is None:
        pw = password or secrets.token_urlsafe(12)
        user = auth.create_user(session, username, pw, realm="demo")
        out["password_created"] = pw
    elif user.realm != "demo":
        raise ValueError(f"user {username!r} exists and is not a demo user")
    budget.get_system(session, "demo")
    out["fixtures"] = seed_fixtures(session, "demo")
    queued = 0
    for mode, specs in DEMO_PLAN.items():
        prof = current_profile(mode)
        for i, (part, skill, band) in enumerate(specs):
            params = {
                "mode": mode,
                "part": part,
                "template_type": part_spec(prof, part)["template_type"],
                "primary_skill": skill,
                "band": band,
                "seed": f"demoseed-{mode}-{i}",
                "source": "demo-generated",
                "pool": "practice",
            }
            _, created = queue.enqueue(session, "demo", "generate", params, dedupe_key=f"demoseed:{mode}:{i}")
            queued += 1 if created else 0
    if with_mock:
        has_set = (
            session.execute(select(m.MockSet).where(m.MockSet.realm == "demo", m.MockSet.mode == mock_modes[0])).first()
            if mock_modes
            else True
        )
        if not has_set:
            for mode in mock_modes:
                prof = current_profile(mode)
                for p in prof["parts"]:
                    for i in range(p["units"]):
                        skill = (
                            "integration"
                            if p["template_type"] in ("lecture_integrated", "condition_choice")
                            else ["explicit_detail", "numbers_negation", "inference", "speaker_attitude"][i % 4]
                        )
                        params = {
                            "mode": mode,
                            "part": p["part"],
                            "template_type": p["template_type"],
                            "primary_skill": skill,
                            "band": 3,
                            "seed": f"demomock-{mode}-{p['part']}-{i}",
                            "source": "demo-generated",
                            "pool": "assessment",
                        }
                        _, created = queue.enqueue(
                            session, "demo", "generate", params, dedupe_key=f"demomock:{mode}:{p['part']}:{i}"
                        )
                        queued += 1 if created else 0
            out["mock_modes"] = list(mock_modes)
    out["jobs_queued"] = queued
    out["username"] = username
    return out


def assemble_demo_mocks(session) -> list[str]:
    from lq.services import assessment

    made = []
    for mode in ("common_test", "cefr_c1"):
        if session.execute(select(m.MockSet).where(m.MockSet.realm == "demo", m.MockSet.mode == mode)).first():
            continue
        try:
            made.append(assessment.assemble(session, "demo", mode).id)
        except assessment.Shortage:
            continue  # visible in the admin inventory; nothing is assembled short
    return made

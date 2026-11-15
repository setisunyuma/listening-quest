"""Unit content schema (schema_version 2), validation, hashing, learner/reviewer views.

The full UnitContent is server-only. `learner_view` strips answers, evidence, feedback and scripts.
`reviewer_view` is what a blind reviewer may see: everything the learner sees or hears, nothing else.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 2

MODES = ("common_test", "eiken_pre1", "eiken_1", "cefr_c1", "cefr_c2")
SKILLS = (
    "main_idea",  # 主旨
    "explicit_detail",  # 明示的な細部
    "numbers_negation",  # 数字・日時・否定
    "condition_matching",  # 条件照合
    "speaker_attitude",  # 話者の意図・態度
    "inference",  # 推論
    "discourse_structure",  # 談話構造
    "integration",  # 複数情報の統合
)
SKILL_LABELS_JA = {
    "main_idea": "主旨",
    "explicit_detail": "明示的な細部",
    "numbers_negation": "数字・日時・否定",
    "condition_matching": "条件照合",
    "speaker_attitude": "話者の意図・態度",
    "inference": "推論",
    "discourse_structure": "談話構造",
    "integration": "複数情報の統合",
}
MODE_LABELS_JA = {
    "common_test": "共通テスト",
    "eiken_pre1": "英検準1級",
    "eiken_1": "英検1級",
    "cefr_c1": "C1",
    "cefr_c2": "C2",
}
SESSION_TYPES = ("train", "review", "radio", "assessment")
UNIT_STATUSES = (
    "DRAFT",
    "CONTENT_REVIEW",
    "AUDIO_PENDING",
    "AUDIO_GENERATING",
    "AUDIO_QA",
    "READY",
    "QUARANTINED",
    "FAILED",
)

_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Speaker(Strict):
    id: _ID
    role: Literal["narrator", "speaker"] = "speaker"
    label: str = ""  # shown to learners, e.g. "M" / "W" / "Student A"
    voice_hint: Literal["male", "female", "neutral"] = "neutral"


class Utterance(Strict):
    id: _ID
    speaker_id: _ID
    lang: Literal["en", "ja"] = "en"
    text: str = Field(min_length=1, max_length=4000)


class StimulusSegment(Strict):
    id: _ID
    label: str = ""
    plays: int = Field(default=1, ge=1, le=3)  # official number of plays
    utterances: list[Utterance] = Field(min_length=1)


class FigureCell(Strict):
    id: _ID
    text: str


class FigureRow(Strict):
    id: _ID
    cells: list[FigureCell]


class Figure(Strict):
    """Structured figure rendered as safe HTML/SVG by the client. No raw HTML is accepted."""

    id: _ID
    kind: Literal["table", "list", "worksheet", "scene_set", "chart_bar"]
    title: str = ""
    columns: list[str] = []
    rows: list[FigureRow] = []
    note: str = ""


class Choice(Strict):
    id: _ID
    text: str = Field(min_length=1, max_length=600)


class SingleChoiceAnswer(Strict):
    type: Literal["single_choice"] = "single_choice"
    choice_id: _ID


class MultiSelectAnswer(Strict):
    type: Literal["multi_select"] = "multi_select"
    choice_ids: list[_ID] = Field(min_length=1)


class OrderingAnswer(Strict):
    type: Literal["ordering"] = "ordering"
    order: list[_ID] = Field(min_length=2)


class MatchingAnswer(Strict):
    type: Literal["matching"] = "matching"
    pairs: dict[str, _ID]  # item id -> choice id


class BlankAnswer(Strict):
    type: Literal["blank"] = "blank"
    accepted: list[str] = Field(min_length=1)


Answer = Annotated[
    SingleChoiceAnswer | MultiSelectAnswer | OrderingAnswer | MatchingAnswer | BlankAnswer,
    Field(discriminator="type"),
]


class EvidenceRef(Strict):
    utterance_id: _ID | None = None
    figure_id: _ID | None = None
    element_id: _ID | None = None  # row or cell id inside the figure

    @model_validator(mode="after")
    def _one(self):
        if not self.utterance_id and not self.figure_id:
            raise ValueError("evidence needs utterance_id or figure_id")
        return self


class DistractorReason(Strict):
    choice_id: _ID
    reason_ja: str = Field(min_length=1)


class Feedback(Strict):
    answer_en: str = Field(min_length=1)  # English statement of the correct answer
    explanation_ja: str = Field(min_length=1, max_length=600)  # short Japanese explanation
    distractors: list[DistractorReason] = []
    hint_ja: str = ""  # optional pre-answer hint (assisted)


class MatchItem(Strict):
    id: _ID
    text: str


class Question(Strict):
    id: _ID
    slot: int = Field(ge=1, le=99)  # answer-slot order inside the unit
    segment_ids: list[_ID] = []  # segments this question depends on (empty = all)
    prompt_en: str = Field(min_length=1)
    prompt_medium: Literal["screen", "audio", "both"] = "screen"
    prompt_timing: Literal["before", "after"] = "before"
    type: Literal["single_choice", "multi_select", "ordering", "matching", "blank"] = "single_choice"
    choices: list[Choice] = []
    items: list[MatchItem] = []  # for matching
    figure_ids: list[_ID] = []
    answer: Answer
    skill_tags: list[str] = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)
    feedback: Feedback


class ScoringGroup(Strict):
    id: _ID
    question_ids: list[_ID] = Field(min_length=1)
    points: float = Field(ge=0)
    all_or_nothing: bool = False


class PreInfo(Strict):
    """Shown before playback (e.g. Eiken Real-Life situation and question)."""

    situation_en: str = ""
    read_seconds: int = Field(default=0, ge=0, le=120)


class PresentationProfile(Strict):
    kind: Literal["official", "audio_arranged", "practice"] = "official"
    choices_read_aloud: bool = False
    figure_verbalized: bool = False
    notes: str = ""


class RadioInfo(Strict):
    supported: bool = True
    reason: str = ""
    figure_description_en: str = ""  # narrated replacement for figures (audio arrangement)


class GlossItem(Strict):
    term: str = Field(min_length=1, max_length=80)
    meaning_ja: str = Field(min_length=1, max_length=300)


class LineTranslation(Strict):
    utterance_id: _ID
    ja: str = Field(min_length=1, max_length=2000)


class StudyNotes(Strict):
    """Learning aids shown only with the results: a short glossary and a Japanese rendering of each line.
    Not part of grading, so not part of the content hash (adding them never changes an existing unit's hash)."""

    glossary: list[GlossItem] = []
    translations: list[LineTranslation] = []


PRACTICE_KINDS = ("P1A", "P1B", "P2A", "P2B", "P3A", "P3B", "P4A", "P4B")
CRITICAL_KINDS = ("subject", "conclusion", "negation", "number", "condition", "attribution", "certainty")


class PracticeItem(Strict):
    """One rubric item of a practice task = one question (統合指示書 M01). Choices that are critical
    misunderstandings are labelled, so good answers elsewhere never offset them."""

    question_id: _ID
    rubric: str = Field(min_length=1, max_length=32)
    main: bool = True
    stage: Literal["initial", "final"] = "final"  # P2B: the provisional answer is kept apart from the final one
    sub_skills: list[str] = []  # versioned sub-skills (curriculum.yaml), mapped onto the 8 common skills
    critical_choices: dict[
        str, Literal["subject", "conclusion", "negation", "number", "condition", "attribution", "certainty"]
    ] = {}
    accept_choice_ids: list[_ID] = []  # further acceptable answers (a set of reasonable actions)
    pending_if_unmatched: bool = False  # short answers: text that matches nothing is "判定保留", never wrong


class Clarification(Strict):
    """A permitted check inside the scene (e.g. asking the office). The reply is pre-authored; asking is recorded
    and never counts as first-time understanding."""

    id: _ID
    label_ja: str = Field(min_length=1, max_length=120)
    reply_en: str = Field(min_length=1, max_length=600)
    informs: list[_ID] = []  # question ids the reply bears on


class PracticeBlock(Strict):
    practice_kind: Literal["P1A", "P1B", "P2A", "P2B", "P3A", "P3B", "P4A", "P4B"]
    curriculum_version: str
    purpose_ja: str = Field(min_length=1, max_length=300)  # what the learner needs to achieve in the scene
    listener_ja: str = ""  # who the result is for (hand-over tasks)
    success_ja: str = Field(min_length=1, max_length=300)
    support_level: int = Field(default=1, ge=1, le=4)
    input_mode: Literal["voice_first", "desk"] = "voice_first"
    scene: str = Field(min_length=1, max_length=48)  # scene / speaker set-up tag (variety is checked on these)
    items: list[PracticeItem] = Field(min_length=1)
    clarifications: list[Clarification] = []
    max_clarifications: int = Field(default=0, ge=0, le=3)
    prosody_required: bool = False  # P4B: scored only after the tone was confirmed on real audio
    prosody_confirmed: bool = False
    audio_needed_checked: bool = False  # Q01 / V15: the key condition was varied and the answer changed with it
    generated: bool = True  # generated scripts are shown as generated material, never as real recordings


class UnitContent(Strict):
    schema_version: Literal[2] = 2
    source: _ID
    content_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,128}$")]
    revision: int = Field(default=1, ge=1)
    mode: Literal["common_test", "eiken_pre1", "eiken_1", "cefr_c1", "cefr_c2"]
    profile_version: str
    part: str
    template_type: str
    title: str = Field(min_length=1, max_length=120)  # must not hint at the answer
    topic: str
    skill_tags: list[str] = Field(min_length=1)
    primary_skill: str
    provisional_difficulty_band: int = Field(ge=1, le=5)
    calibration_status: Literal["provisional", "calibrated"] = "provisional"
    family_id: str = Field(min_length=1, max_length=128)
    variant_of: str | None = None
    presentation_profile: PresentationProfile = PresentationProfile()
    speakers: list[Speaker] = Field(min_length=1)
    pre_info: PreInfo | None = None
    figures: list[Figure] = []
    segments: list[StimulusSegment] = Field(min_length=1)
    questions: list[Question] = []
    legacy_radio_only: bool = False  # answer-included complete audio only; never an answering candidate
    scoring_groups: list[ScoringGroup] = []
    radio: RadioInfo = RadioInfo()
    estimated_duration_sec: int | None = None
    practice: PracticeBlock | None = None  # practice track only (統合指示書 A01); never part of exam scoring
    study_notes: StudyNotes = StudyNotes()

    @model_validator(mode="after")
    def _check(self):
        errs: list[str] = []
        if not self.questions and not self.legacy_radio_only:
            errs.append("a unit needs at least one question (unless legacy_radio_only)")
        if self.primary_skill not in self.skill_tags:
            errs.append("primary_skill must be one of skill_tags")
        for s in self.skill_tags:
            if s not in SKILLS:
                errs.append(f"unknown skill tag: {s}")
        speaker_ids = {s.id for s in self.speakers}
        if len(speaker_ids) != len(self.speakers):
            errs.append("duplicate speaker id")
        utt_ids: set[str] = set()
        seg_ids: set[str] = set()
        for seg in self.segments:
            if seg.id in seg_ids:
                errs.append(f"duplicate segment id {seg.id}")
            seg_ids.add(seg.id)
            for u in seg.utterances:
                if u.id in utt_ids:
                    errs.append(f"duplicate utterance id {u.id}")
                utt_ids.add(u.id)
                if u.speaker_id not in speaker_ids:
                    errs.append(f"utterance {u.id}: unknown speaker {u.speaker_id}")
        fig_elems: dict[str, set[str]] = {}
        for f in self.figures:
            elems = set()
            for r in f.rows:
                elems.add(r.id)
                elems.update(c.id for c in r.cells)
            fig_elems[f.id] = elems
        qids: set[str] = set()
        slots: set[int] = set()
        for q in self.questions:
            if q.id in qids:
                errs.append(f"duplicate question id {q.id}")
            qids.add(q.id)
            if q.slot in slots:
                errs.append(f"duplicate slot {q.slot}")
            slots.add(q.slot)
            for s in q.skill_tags:
                if s not in SKILLS:
                    errs.append(f"question {q.id}: unknown skill {s}")
            for sid in q.segment_ids:
                if sid not in seg_ids:
                    errs.append(f"question {q.id}: unknown segment {sid}")
            for fid in q.figure_ids:
                if fid not in fig_elems:
                    errs.append(f"question {q.id}: unknown figure {fid}")
            if q.answer.type != q.type:
                errs.append(f"question {q.id}: answer type {q.answer.type} != question type {q.type}")
            cids = [c.id for c in q.choices]
            if len(set(cids)) != len(cids):
                errs.append(f"question {q.id}: duplicate choice id")
            a = q.answer
            if a.type == "single_choice":
                if len(cids) < 2 or a.choice_id not in cids:
                    errs.append(f"question {q.id}: answer not among choices")
            elif a.type == "multi_select":
                if not set(a.choice_ids) <= set(cids) or len(set(a.choice_ids)) != len(a.choice_ids):
                    errs.append(f"question {q.id}: multi_select answer invalid")
            elif a.type == "ordering":
                if sorted(a.order) != sorted(cids):
                    errs.append(f"question {q.id}: ordering must be a permutation of choices")
            elif a.type == "matching":
                if set(a.pairs) != {i.id for i in q.items} or not set(a.pairs.values()) <= set(cids):
                    errs.append(f"question {q.id}: matching pairs must cover items with valid choices")
            for ev in q.evidence:
                if ev.utterance_id and ev.utterance_id not in utt_ids:
                    errs.append(f"question {q.id}: evidence references missing utterance {ev.utterance_id}")
                if ev.figure_id:
                    if ev.figure_id not in fig_elems:
                        errs.append(f"question {q.id}: evidence references missing figure {ev.figure_id}")
                    elif ev.element_id and ev.element_id not in fig_elems[ev.figure_id]:
                        errs.append(f"question {q.id}: evidence references missing figure element {ev.element_id}")
            for d in q.feedback.distractors:
                if d.choice_id not in cids:
                    errs.append(f"question {q.id}: distractor reason for unknown choice {d.choice_id}")
        grouped: set[str] = set()
        for g in self.scoring_groups:
            for qid in g.question_ids:
                if qid not in qids:
                    errs.append(f"scoring group {g.id}: unknown question {qid}")
                if qid in grouped:
                    errs.append(f"question {qid} is in more than one scoring group")
                grouped.add(qid)
        if self.pre_info is None and self.part.startswith("part3") and self.mode.startswith("eiken"):
            errs.append("Eiken Real-Life units need pre_info")
        if self.practice is not None:
            qmap = {q.id: q for q in self.questions}
            if self.presentation_profile.kind != "practice":
                errs.append("practice material uses the practice presentation (never official)")
            seen = set()
            for it in self.practice.items:
                q = qmap.get(it.question_id)
                if q is None:
                    errs.append(f"practice item: unknown question {it.question_id}")
                    continue
                seen.add(it.question_id)
                cids = {c.id for c in q.choices}
                for cid in [*it.critical_choices, *it.accept_choice_ids]:
                    if cid not in cids:
                        errs.append(f"practice item {it.question_id}: unknown choice {cid}")
                if q.type == "single_choice" and q.answer.choice_id in it.critical_choices:
                    errs.append(f"practice item {it.question_id}: the answer cannot be a critical error")
            if seen != set(qmap):
                errs.append("every question of a practice task needs a rubric item")
            for c in self.practice.clarifications:
                for qid in c.informs:
                    if qid not in qmap:
                        errs.append(f"clarification {c.id}: unknown question {qid}")
            if len(self.practice.clarifications) and not self.practice.max_clarifications:
                errs.append("clarifications are listed but max_clarifications is 0")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    # ---- helpers
    def utterance_map(self) -> dict[str, Utterance]:
        return {u.id: u for seg in self.segments for u in seg.utterances}

    def question_map(self) -> dict[str, Question]:
        return {q.id: q for q in self.questions}

    def sorted_questions(self) -> list[Question]:
        return sorted(self.questions, key=lambda q: q.slot)


# ---------------------------------------------------------------- hashing

_WS = re.compile(r"\s+")


def _norm_text(v: Any) -> Any:
    if isinstance(v, str):
        return _WS.sub(" ", v).strip()
    if isinstance(v, list):
        return [_norm_text(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm_text(x) for k, x in sorted(v.items())}
    return v


def canonical_json(obj: Any) -> str:
    return json.dumps(_norm_text(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# Fields that carry meaning for the learner or for grading. Deliberately excluded: source, content_id,
# revision, title, estimated_duration_sec, calibration_status (bookkeeping), plus anything stored outside the
# content (review records, the hash itself, timestamps, cost logs, audio storage keys).
HASHED_FIELDS = (
    "schema_version",
    "mode",
    "profile_version",
    "part",
    "template_type",
    "presentation_profile",
    "speakers",
    "pre_info",
    "figures",
    "segments",
    "questions",
    "scoring_groups",
    "radio",
    "legacy_radio_only",
)


def content_hash(unit: UnitContent) -> str:
    data = unit.model_dump(mode="json")
    hashed = {k: data[k] for k in HASHED_FIELDS}
    if data.get("practice") is not None:  # only practice material carries it: existing hashes do not change
        hashed["practice"] = data["practice"]
    return sha256_hex(canonical_json(hashed))


def notes_hash(unit: UnitContent) -> str:
    """R08: the learning notes (translations, glossary) have their own hash and audit state. They are not part of
    the content hash, so correcting a translation never re-grades history or re-makes audio."""
    return sha256_hex(canonical_json(unit.model_dump(mode="json")["study_notes"]))


def stimulus_fingerprint(unit: UnitContent) -> str:
    """Identifies the script/audio regardless of which questions are attached."""
    script = [[u.speaker_id, u.lang, u.text.lower()] for seg in unit.segments for u in seg.utterances]
    return sha256_hex(canonical_json(script))


def word_count(unit: UnitContent) -> int:
    return sum(len(u.text.split()) for seg in unit.segments for u in seg.utterances)


def estimate_duration_sec(unit: UnitContent, segment_ms: dict[str, int] | None = None) -> int:
    """Listening (official repeats) + answering + feedback review. A planning estimate, not a guarantee."""
    listen = 0.0
    for seg in unit.segments:
        if segment_ms and seg.id in segment_ms:
            one = segment_ms[seg.id] / 1000
        else:
            one = sum(len(u.text.split()) for u in seg.utterances) / 2.6 + 0.6 * len(seg.utterances)
        listen += one * seg.plays
    pre = unit.pre_info.read_seconds if unit.pre_info else 0
    n = len(unit.questions)
    return int(round(listen + pre + 12 * n + 25 * n))


# ---------------------------------------------------------------- views


def display_order(ids: list[str], seed: str) -> list[str]:
    """A fixed, per-attempt order for showing choices (practice tasks): the same attempt always gets the same order
    (resume, reload, results), different attempts get different orders, and answers stay keyed by choice id."""
    return sorted(ids, key=lambda cid: sha256_hex(f"{seed}:{cid}"))


def shuffle_view_choices(view_questions: list[dict], seed: str) -> None:
    for q in view_questions:
        if q.get("type") == "single_choice" and len(q.get("choices") or []) > 1:
            by = {c["id"]: c for c in q["choices"]}
            q["choices"] = [by[i] for i in display_order(list(by), f"{seed}:{q['id']}")]


def _rubric_label(kind: str, rubric: str) -> str:
    from lq.domain.practice import task_def

    return next((r["label_ja"] for r in task_def(kind)["rubric"] if r["id"] == rubric), rubric)


def learner_view(unit: UnitContent, *, show_audio_prompts: bool = False) -> dict:
    """Learner DTO: no answers, evidence, feedback, hints or scripts.

    Prompts that the official format presents by audio only are withheld unless `show_audio_prompts`
    (an explicit, recorded aid) is set.
    """
    questions = []
    for q in unit.sorted_questions():
        visible = q.prompt_medium in ("screen", "both") or show_audio_prompts
        questions.append(
            {
                "id": q.id,
                "slot": q.slot,
                "type": q.type,
                "prompt_en": q.prompt_en if visible else None,
                "prompt_medium": q.prompt_medium,
                "prompt_timing": q.prompt_timing,
                "segment_ids": q.segment_ids,
                "choices": [{"id": c.id, "text": c.text} for c in q.choices],
                "items": [{"id": i.id, "text": i.text} for i in q.items],
                "figure_ids": q.figure_ids,
                "has_hint": bool(q.feedback.hint_ja),
            }
        )
    return {
        "mode": unit.mode,
        "part": unit.part,
        "template_type": unit.template_type,
        "title": unit.title,
        "presentation_profile": unit.presentation_profile.model_dump(),
        "pre_info": unit.pre_info.model_dump() if unit.pre_info else None,
        "speakers": [{"id": s.id, "label": s.label, "role": s.role} for s in unit.speakers],
        "figures": [f.model_dump() for f in unit.figures],
        "segments": [{"id": s.id, "label": s.label, "plays": s.plays} for s in unit.segments],
        "questions": questions,
        # practice tasks: what to achieve and which checks are permitted (never the replies or answers)
        "practice": None
        if unit.practice is None
        else {
            "kind": unit.practice.practice_kind,
            "purpose_ja": unit.practice.purpose_ja,
            "listener_ja": unit.practice.listener_ja,
            "success_ja": unit.practice.success_ja,
            "input_mode": unit.practice.input_mode,
            "max_clarifications": unit.practice.max_clarifications,
            "clarifications": [{"id": c.id, "label_ja": c.label_ja} for c in unit.practice.clarifications],
            "items": [
                {
                    "question_id": it.question_id,
                    "rubric": it.rubric,
                    "label_ja": _rubric_label(unit.practice.practice_kind, it.rubric),
                    "main": it.main,
                    "stage": it.stage,
                }
                for it in unit.practice.items
            ],
            "generated": unit.practice.generated,
        },  # fmt: skip
    }


def reviewer_view(unit: UnitContent) -> dict:
    """Blind-review payload: what the learner sees and hears. No answer, evidence, feedback, title,
    skill tags, family or ids that could reveal the author's key."""
    return {
        "mode": unit.mode,
        "part": unit.part,
        "pre_info": unit.pre_info.situation_en if unit.pre_info else "",
        "figures": [
            {
                "id": f.id,
                "kind": f.kind,
                "title": f.title,
                "columns": f.columns,
                "rows": [[c.text for c in r.cells] for r in f.rows],
                "note": f.note,
            }
            for f in unit.figures
        ],
        "audio_script": [
            {"segment": seg.id, "lines": [{"speaker": u.speaker_id, "text": u.text} for u in seg.utterances]}
            for seg in unit.segments
        ],
        "questions": [
            {
                "id": q.id,
                "type": q.type,
                "prompt": q.prompt_en,
                "choices": [{"id": c.id, "text": c.text} for c in q.choices],
                "items": [{"id": i.id, "text": i.text} for i in q.items],
            }
            for q in unit.sorted_questions()
        ],
    }


# ---------------------------------------------------------------- grading


def normalize_answer(q: Question, raw: Any) -> dict | None:
    """Validate a submitted answer shape. None means an explicit skip."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"question {q.id}: answer must be an object or null")
    cids = {c.id for c in q.choices}
    t = q.type
    if t == "single_choice":
        cid = raw.get("choice_id")
        if cid not in cids:
            raise ValueError(f"question {q.id}: unknown choice")
        return {"type": t, "choice_id": cid}
    if t == "multi_select":
        ids = raw.get("choice_ids")
        if not isinstance(ids, list) or not set(ids) <= cids:
            raise ValueError(f"question {q.id}: invalid choices")
        return {"type": t, "choice_ids": sorted(set(ids))}
    if t == "ordering":
        order = raw.get("order")
        if not isinstance(order, list) or sorted(order) != sorted(cids):
            raise ValueError(f"question {q.id}: order must use every choice once")
        return {"type": t, "order": order}
    if t == "matching":
        pairs = raw.get("pairs")
        if not isinstance(pairs, dict) or set(pairs) != {i.id for i in q.items} or not set(pairs.values()) <= cids:
            raise ValueError(f"question {q.id}: invalid pairs")
        return {"type": t, "pairs": dict(sorted(pairs.items()))}
    if t == "blank":
        text = raw.get("text")
        if not isinstance(text, str):
            raise ValueError(f"question {q.id}: text required")
        return {"type": t, "text": text.strip()}
    raise ValueError(f"unsupported type {t}")


def is_correct(q: Question, ans: dict | None) -> bool:
    if ans is None:
        return False
    a = q.answer
    if a.type == "single_choice":
        return ans.get("choice_id") == a.choice_id
    if a.type == "multi_select":
        return sorted(ans.get("choice_ids", [])) == sorted(a.choice_ids)
    if a.type == "ordering":
        return ans.get("order") == a.order
    if a.type == "matching":
        return ans.get("pairs") == dict(sorted(a.pairs.items()))
    if a.type == "blank":
        norm = _WS.sub(" ", ans.get("text", "")).strip().lower()
        return norm in {_WS.sub(" ", x).strip().lower() for x in a.accepted}
    return False


def unit_json_schema() -> dict:
    return UnitContent.model_json_schema()

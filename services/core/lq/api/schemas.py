"""Request / response models. The OpenAPI document generated from these is the contract the web app's
TypeScript types are generated from (packages/contracts)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EventId = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    username: str
    realm: Literal["live", "demo"]
    user_id: str
    csrf_token: str
    settings: dict[str, Any]


class SettingsPatch(BaseModel):
    active_mode: str | None = None
    daily_goal_minutes: int | None = None
    timezone: str | None = None
    day_cutoff_hour: int | None = None
    playback_rate: float | None = None
    sfx: bool | None = None
    reduce_effects: bool | None = None
    primary_side: Literal["left", "right"] | None = None
    font_scale: float | None = None
    radio_include_new: bool | None = None


class AudioRef(BaseModel):
    url: str
    duration_ms: int
    bytes: int | None = None
    sha256: str | None = None


class LearnerChoice(BaseModel):
    id: str
    text: str


class LearnerQuestion(BaseModel):
    id: str
    slot: int
    type: str
    prompt_en: str | None
    prompt_medium: str
    prompt_timing: str
    segment_ids: list[str]
    choices: list[LearnerChoice]
    items: list[LearnerChoice]
    figure_ids: list[str]
    has_hint: bool
    global_slot: int | None = None


class LearnerSegment(BaseModel):
    id: str
    label: str
    plays: int
    audio: AudioRef | None = None


class LearnerUnit(BaseModel):
    """What an answering session may see. By construction there are no answer, evidence, feedback or script fields."""

    mode: str
    part: str
    template_type: str
    title: str | None = None
    presentation_profile: dict[str, Any]
    pre_info: dict[str, Any] | None
    speakers: list[dict[str, Any]]
    figures: list[dict[str, Any]]
    segments: list[LearnerSegment]
    questions: list[LearnerQuestion]


class AttemptStartIn(BaseModel):
    attempt_id: str = EventId
    unit_id: str
    slot_id: str | None = None


class AttemptOut(BaseModel):
    attempt_id: str
    unit_id: str
    unit_revision: int
    status: str
    session_type: str
    presentation: str
    counts_as_first: bool
    slot_id: str | None
    plays: dict[str, int]
    aids: dict[str, bool]
    audio_kind: str
    band: int
    unit: LearnerUnit


class PlayIn(BaseModel):
    event_id: str = EventId
    segment_id: str
    rate: float = 1.0


class PlayOut(BaseModel):
    plays: dict[str, int] = {}
    aids: dict[str, bool] = {}
    allowed_plays: dict[str, int] = {}


class AidIn(BaseModel):
    event_id: str = EventId
    kind: Literal["transcript", "hint", "prompt_text"]


class SubmitIn(BaseModel):
    answers: dict[str, dict[str, Any] | None]


class ResultQuestion(BaseModel):
    id: str
    slot: int
    type: str
    prompt_en: str
    choices: list[LearnerChoice]
    items: list[LearnerChoice]
    your_answer: dict[str, Any] | None
    skipped: bool
    correct: bool
    correct_answer: dict[str, Any]
    answer_en: str
    explanation_ja: str
    distractors: dict[str, str]
    evidence: list[dict[str, Any]]
    skill_tags: list[str]
    system_guess: str | None
    system_guess_label: str | None
    self_reason: str | None
    global_slot: int | None = None


class ResultOut(BaseModel):
    attempt_id: str
    unit_id: str
    status: str
    session_type: str
    category: str | None
    complete: bool
    correct_count: int
    total: int
    counted_as_first: bool
    not_first_reasons: list[str]
    assisted: bool
    presentation: str
    schedule: dict[str, Any] | None
    band: dict[str, Any] | None
    feedback_audio: AudioRef | None
    transcript: list[dict[str, Any]]
    figures: list[dict[str, Any]]
    questions: list[ResultQuestion]
    feedback_viewed: bool
    slot_id: str | None
    audio_kind: str
    title: str


class AckOut(BaseModel):
    awarded: list[dict[str, Any]]
    total_xp: int
    level: int
    slot_completed: bool
    plan_completed: bool
    needs_all_answers: bool


class ErrorReasonIn(BaseModel):
    question_id: str
    reason: Literal["sound", "vocabulary", "numbers_negation", "inference", "organizing", "focus", "other"] | None


class UnitCard(BaseModel):
    id: str
    title: str
    mode: str
    part: str
    part_label: str
    template_type: str
    band: int
    question_count: int
    est_sec: int
    audio_kind: str
    primary_skill: str


class PlanSlotOut(BaseModel):
    id: str
    idx: int
    category: str
    mode: str
    state: str
    reserved: bool
    assignment_version: int
    est_sec: int
    time_cap_sec: int
    reason_text: str
    reason_code: str
    unit: UnitCard | None
    unit_revision: int | None
    attempt_id: str | None
    attempt_status: str | None
    needs_all_answers: bool
    awaiting_feedback_ack: bool


class PlanOut(BaseModel):
    id: str
    learning_day: str
    kind: str
    mode: str
    status: str
    version: int
    goal_minutes: int
    budget_sec: int
    planned_sec: int
    total_slots: int
    completed_slots: int
    remaining_sec: int
    notes: list[str]
    radio_program_id: str | None
    slots: list[PlanSlotOut]


class PlanStartIn(BaseModel):
    kind: Literal["quest", "radio"] = "quest"
    mode: str | None = None


class RebuildIn(BaseModel):
    mode: str


class HomeOut(BaseModel):
    today: str
    settings: dict[str, Any]
    realm: str
    username: str
    plan: PlanOut | None
    preview: dict[str, Any] | None
    carried_plan: PlanOut | None
    xp: dict[str, int]
    streak_days: int
    recent: list[dict[str, Any]]
    modes: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    exam_label: str


class LibraryCard(UnitCard):
    topic: str
    skill_tags: list[str]
    session_types: list[str]
    status: str
    status_label: str
    badge: str | None
    badge_label: str | None
    due_day: str | None
    stage: int | None
    attempts: int
    last_score: str | None
    presentation: str
    calibration_status: str
    ready_at: str | None


class LibraryOut(BaseModel):
    units: list[LibraryCard]
    count: int
    generated_at: str


class OfflineAnswer(BaseModel):
    event_id: str = EventId
    unit_id: str
    unit_revision: int
    answers: dict[str, dict[str, Any] | None]
    plays: dict[str, int] = {}
    rates: dict[str, float] = {}
    plan_id: str | None = None
    slot_id: str | None = None
    assignment_version: int | None = None
    client_started_at: str | None = None
    client_answered_at: str | None = None


class SyncIn(BaseModel):
    items: list[OfflineAnswer] = Field(max_length=200)


class RadioProgramIn(BaseModel):
    mode: str
    minutes: int | None = Field(default=None, ge=1, le=120)
    unit_ids: list[str] | None = None
    include_new: bool | None = None


class RadioProgressIn(BaseModel):
    event_id: str = EventId
    ranges: list[tuple[int, int]] = Field(max_length=500)
    position_ms: int = Field(ge=0)


class SelfReportIn(BaseModel):
    event_id: str = EventId
    unit_id: str
    value: Literal["correct", "incorrect", "unknown"]
    session_id: str | None = None


class UnitIdIn(BaseModel):
    unit_id: str


class AssessmentStartIn(BaseModel):
    mock_set_id: str


class AssessmentSubmitIn(BaseModel):
    answers: dict[str, dict[str, Any] | None]


class SystemPatch(BaseModel):
    paid_enabled: bool | None = None
    daily_budget_usd: float | None = None
    per_job_cap_usd: float | None = None
    max_concurrent_jobs: int | None = None
    auto_replenish: bool | None = None
    replenish_modes: list[str] | None = None


class GenerateIn(BaseModel):
    mode: str
    part: str
    primary_skill: str
    band: int = Field(ge=1, le=5)
    count: int = Field(default=1, ge=1, le=20)
    topic_hint: str = Field(default="", max_length=120)
    pool: Literal["practice", "assessment"] = "practice"


class ApproveIn(BaseModel):
    target: Literal["content", "audio"]
    note: str = Field(default="", max_length=300)


class InvalidateIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ModeIn(BaseModel):
    mode: str


class RestoreIn(BaseModel):
    export: dict[str, Any]
    apply_settings: bool = False

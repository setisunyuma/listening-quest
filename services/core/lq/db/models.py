"""SQLAlchemy models.

Conventions
- ids are 32-char hex strings. Client-supplied ids (attempt_id / event_id) use the same shape.
- `realm` is "live" or "demo"; every learner-facing query filters by the user's realm.
- A Unit row is one immutable *version* of a learning unit. Segments / utterances / questions /
  scoring groups / feedback are typed Pydantic structures (lq.domain.content) stored in `content`.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lq.db.base import Base, new_id, utcnow

ID = String(64)


def pk() -> Mapped[str]:
    return mapped_column(ID, primary_key=True, default=new_id)


def ts(nullable: bool = False, default: bool = True) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=nullable, default=utcnow if default else None)


# ---------------------------------------------------------------- users / auth


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    realm: Mapped[str] = mapped_column(String(8), default="live")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = pk()
    key: Mapped[str] = mapped_column(String(128), index=True)  # username|ip
    success: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = ts()


class SystemSetting(Base):
    """Per-realm admin settings (paid generation, budgets, auto-replenish)."""

    __tablename__ = "system_settings"
    realm: Mapped[str] = mapped_column(String(8), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = ts()


# ---------------------------------------------------------------- content


class ModeProfile(Base):
    __tablename__ = "mode_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. common_test/2026_main
    mode: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(32))
    spec: Mapped[dict] = mapped_column(JSONB)
    spec_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = ts()


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("realm", "source", "content_id", "revision", name="uq_unit_version"),
        Index("ix_units_ready", "realm", "mode", "status"),
    )
    id: Mapped[str] = pk()
    realm: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(64))
    content_id: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    mode: Mapped[str] = mapped_column(String(32))
    profile_version: Mapped[str] = mapped_column(String(64))
    part: Mapped[str] = mapped_column(String(32))
    template_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    skill_tags: Mapped[list] = mapped_column(JSONB)
    primary_skill: Mapped[str] = mapped_column(String(32))
    topic: Mapped[str] = mapped_column(String(64))
    difficulty_band: Mapped[int] = mapped_column(Integer)  # provisional band 1..5
    calibration_status: Mapped[str] = mapped_column(String(32), default="provisional")
    family_id: Mapped[str] = mapped_column(String(128), index=True)
    stimulus_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    variant_of: Mapped[str | None] = mapped_column(String(128), nullable=True)
    presentation_profile: Mapped[dict] = mapped_column(JSONB)
    estimated_duration_sec: Mapped[int] = mapped_column(Integer)
    question_count: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)  # full server-side content (answers included)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_types: Mapped[list] = mapped_column(JSONB, default=list)  # allowed: train/review/radio/assessment
    pool: Mapped[str] = mapped_column(String(16), default="practice")  # practice | assessment
    audio_kind: Mapped[str] = mapped_column(String(16), default="none")  # none | demo_tone | tts | imported
    audio_review_state: Mapped[str] = mapped_column(
        String(24), default="none"
    )  # none|auto_checked|human_listened|mock_checked
    superseded_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts()
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Asset(Base):
    """A stored audio object. role examples: utt:<utterance_id>, stimulus:<segment_id>, exam, feedback, radio."""

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("unit_id", "role", "version", name="uq_asset_role"),)
    id: Mapped[str] = pk()
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # utterance|stimulus|exam|feedback|radio|program
    role: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(32), default="audio/mpeg")
    quality_state: Mapped[str] = mapped_column(String(24), default="unchecked")  # unchecked|decoded_ok|failed
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # timeline / transcript (server only)
    created_at: Mapped[datetime] = ts()


class AudioCache(Base):
    """Finished + verified utterance audio keyed by a hash of every synthesis input."""

    __tablename__ = "audio_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    voice: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = ts()


class ReviewRecord(Base):
    __tablename__ = "review_records"
    id: Mapped[str] = pk()
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(16))  # content | audio
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    # content: api_reviewed | manual_reviewed | author_reviewed | demo_mock_reviewed
    # audio:   audio_auto_checked | audio_human_listened | audio_demo_mock_checked
    method: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str] = mapped_column(String(128))  # person, or provider:model
    result: Mapped[str] = mapped_column(String(16))  # pass | fail
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts()


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)
    id: Mapped[str] = pk()
    realm: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(24))  # generate | process_unit
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="QUEUED"
    )  # QUEUED|RUNNING|BLOCKED|RETRY_WAIT|FAILED|SUCCEEDED
    blocked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_completed_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = ts()
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unit_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)  # resumable step data
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = pk()
    realm: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # accepted | duplicate | conflict | rejected
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts()


class CostEntry(Base):
    """One paid (or would-be-paid) provider request. state: reserved -> settled | unknown | released."""

    __tablename__ = "cost_ledger"
    id: Mapped[str] = pk()
    realm: Mapped[str] = mapped_column(String(8))
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    step: Mapped[str] = mapped_column(String(16))  # content | review | tts | asr
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    budget_day: Mapped[date] = mapped_column(Date, index=True)  # UTC day
    estimated_usd: Mapped[float] = mapped_column(Float)
    actual_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    price_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_checked_at: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = ts()


# ---------------------------------------------------------------- learning records


class ModeState(Base):
    __tablename__ = "mode_states"
    __table_args__ = (UniqueConstraint("user_id", "mode", name="uq_mode_state"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(32))
    difficulty_band: Mapped[int] = mapped_column(Integer, default=3)
    band_since: Mapped[datetime] = ts()
    last_band_change_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    band_history: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = ts()


class LearningSession(Base):
    __tablename__ = "learning_sessions"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_type: Mapped[str] = mapped_column(String(16))  # train | radio | review | assessment
    mode: Mapped[str] = mapped_column(String(32))
    plan_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    mock_set_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | submitted | abandoned
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    submit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = ts()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Attempt(Base):
    """One pass through one Unit. The id is supplied by the client so retries are idempotent."""

    __tablename__ = "attempts"
    __table_args__ = (
        # only one attempt per user may ever hold the "first exposure" claim for a stimulus
        Index(
            "uq_attempt_first_claim",
            "user_id",
            "stimulus_fingerprint",
            unique=True,
            postgresql_where=text("first_claim"),
        ),
        Index("ix_attempt_user_unit", "user_id", "unit_id"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"))
    stimulus_fingerprint: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    session_type: Mapped[str] = mapped_column(String(16))
    slot_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    presentation: Mapped[str] = mapped_column(String(16), default="official")  # official | arranged
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | submitted
    first_claim: Mapped[bool] = mapped_column(Boolean, default=False)
    eligible_at_start: Mapped[bool] = mapped_column(Boolean, default=False)
    family_seen_before: Mapped[bool] = mapped_column(Boolean, default=False)
    review_was_due: Mapped[bool] = mapped_column(Boolean, default=False)
    band_at_start: Mapped[int] = mapped_column(Integer, default=3)
    plays: Mapped[dict] = mapped_column(JSONB, default=dict)  # segment_id -> count
    aids: Mapped[dict] = mapped_column(JSONB, default=dict)  # extra_replay/speed/transcript/hint/prompt_text -> True
    answers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answer_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # per-question correctness etc.
    complete: Mapped[bool] = mapped_column(Boolean, default=False)  # every target question answered (no skips)
    unit_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # mean correctness, complete only
    strict_first: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # first|assisted|review|repeat|assessment|partial
    not_first_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    error_reasons: Mapped[dict] = mapped_column(JSONB, default=dict)  # self-declared, per question
    offline: Mapped[bool] = mapped_column(Boolean, default=False)
    client_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    learning_day: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = ts()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExposureEvent(Base):
    __tablename__ = "exposure_events"
    __table_args__ = (
        Index("ix_exposure_user_fp", "user_id", "stimulus_fingerprint"),
        Index("ix_exposure_user_family", "user_id", "family_id"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    stimulus_fingerprint: Mapped[str] = mapped_column(String(64))
    family_id: Mapped[str] = mapped_column(String(128))
    # stimulus_play | transcript | hint | prompt_text | answer_access | radio_access | radio_play | assessment
    kind: Mapped[str] = mapped_column(String(24))
    session_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    at: Mapped[datetime] = ts()


class SkillObservation(Base):
    """kind=strict_first: calibrating evidence (one per unit+skill). kind=need_signal: 'missed or needed help just now'."""

    __tablename__ = "skill_observations"
    __table_args__ = (UniqueConstraint("attempt_id", "skill", "kind", name="uq_skill_obs"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ID)
    skill: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    presentation: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    band: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = ts()


class ReviewSchedule(Base):
    __tablename__ = "review_schedules"
    __table_args__ = (UniqueConstraint("user_id", "unit_id", "kind", name="uq_schedule"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"))
    kind: Mapped[str] = mapped_column(String(16), default="recall")  # recall | relisten
    stage: Mapped[int] = mapped_column(Integer, default=0)
    due_day: Mapped[date] = mapped_column(Date)
    last_result: Mapped[str | None] = mapped_column(String(24), nullable=True)
    history: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = ts()


class SelfReport(Base):
    __tablename__ = "self_reports"
    id: Mapped[str] = mapped_column(ID, primary_key=True)  # client event id
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"))
    session_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    value: Mapped[str] = mapped_column(String(16))  # correct | incorrect | unknown
    created_at: Mapped[datetime] = ts()


class DailyPlan(Base):
    __tablename__ = "daily_plans"
    __table_args__ = (UniqueConstraint("user_id", "learning_day", name="uq_plan_day"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    learning_day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(8), default="quest")  # quest | radio
    mode: Mapped[str] = mapped_column(String(32))
    goal_minutes: Mapped[int] = mapped_column(Integer)
    budget_sec: Mapped[int] = mapped_column(Integer)
    total_slots: Mapped[int] = mapped_column(Integer)
    planned_sec: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | completed
    version: Mapped[int] = mapped_column(Integer, default=1)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)  # seed, split, shortage notes
    radio_program_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = ts()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanSlot(Base):
    __tablename__ = "plan_slots"
    __table_args__ = (Index("ix_slot_plan", "plan_id", "idx"),)
    id: Mapped[str] = pk()
    plan_id: Mapped[str] = mapped_column(ForeignKey("daily_plans.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(16))  # review | weak | explore | radio
    mode: Mapped[str] = mapped_column(String(32))
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    time_cap_sec: Mapped[int] = mapped_column(Integer)
    est_sec: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending | started | completed | removed
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    assignment_version: Mapped[int] = mapped_column(Integer, default=1)
    reason_code: Mapped[str] = mapped_column(String(32), default="")
    reason_text: Mapped[str] = mapped_column(String(200), default="")
    reason_params: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempt_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class XpEvent(Base):
    __tablename__ = "xp_events"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # unit_base | review_bonus | radio_unit | daily_plan
    unit_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    learning_day: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = ts()


class RadioProgram(Base):
    """A pre-rendered continuous file (several unit radio tracks) built when the learner asks for it."""

    __tablename__ = "radio_programs"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    unit_ids: Mapped[list] = mapped_column(JSONB)
    asset_id: Mapped[str] = mapped_column(ID)
    timeline: Mapped[list] = mapped_column(JSONB)  # [{unit_id, start_ms, end_ms, title}]
    duration_ms: Mapped[int] = mapped_column(Integer)
    learning_day: Mapped[date] = mapped_column(Date)
    session_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    played_ranges: Mapped[list] = mapped_column(JSONB, default=list)  # union of [start_ms, end_ms]
    position_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = ts()


class MockSet(Base):
    __tablename__ = "mock_sets"
    id: Mapped[str] = pk()
    realm: Mapped[str] = mapped_column(String(8))
    mode: Mapped[str] = mapped_column(String(32))
    profile_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))  # full_mock | app_check
    status: Mapped[str] = mapped_column(String(16), default="READY")  # READY | USED | STALE
    items: Mapped[list] = mapped_column(JSONB)  # [{unit_id, part, slots:{question_id: slot}}]
    duration_sec: Mapped[int] = mapped_column(Integer)
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts()


class ClientEvent(Base):
    """Generic idempotency record for client events (plays, aids, acks, radio progress, self reports)."""

    __tablename__ = "client_events"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = pk()
    realm: Mapped[str] = mapped_column(String(8))
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts()

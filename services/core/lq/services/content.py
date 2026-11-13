"""Unit registration (immutable versions), review records, approval, invalidation, READY finalisation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from lq.audio.render import radio_supported
from lq.db import models as m
from lq.domain.content import UnitContent, content_hash, estimate_duration_sec, sha256_hex, stimulus_fingerprint
from lq.storage import get_storage

DEMO_ONLY_METHODS = ("demo_mock_reviewed", "audio_demo_mock_checked")
CONTENT_METHODS = ("api_reviewed", "manual_reviewed", "author_reviewed", "demo_mock_reviewed")
AUDIO_METHODS = ("audio_auto_checked", "audio_human_listened", "audio_demo_mock_checked")


class ContentConflict(Exception):
    pass


def load_content(unit: m.Unit) -> UnitContent:
    return UnitContent.model_validate(unit.content)


def register_unit(session, realm: str, content: UnitContent, *, pool: str = "practice") -> tuple[m.Unit, str]:
    """Idempotent on (realm, source, content_id, revision). Same key + same hash -> 'duplicate';
    same key + different hash -> ContentConflict. An answered version is never overwritten."""
    h = content_hash(content)
    existing = session.execute(
        select(m.Unit).where(
            m.Unit.realm == realm,
            m.Unit.source == content.source,
            m.Unit.content_id == content.content_id,
            m.Unit.revision == content.revision,
        )
    ).scalar_one_or_none()
    if existing:
        if existing.content_hash == h:
            return existing, "duplicate"
        raise ContentConflict(
            f"{content.source}/{content.content_id} rev {content.revision} already exists with different content (existing {existing.content_hash[:12]}, new {h[:12]}); send it as a new revision"
        )
    data = content.model_dump(mode="json")
    unit = m.Unit(
        realm=realm, source=content.source, content_id=content.content_id, revision=content.revision, schema_version=content.schema_version,
        mode=content.mode, profile_version=content.profile_version, part=content.part, template_type=content.template_type, title=content.title,
        skill_tags=content.skill_tags, primary_skill=content.primary_skill, topic=content.topic, difficulty_band=content.provisional_difficulty_band,
        calibration_status=content.calibration_status, family_id=content.family_id, stimulus_fingerprint=stimulus_fingerprint(content),
        variant_of=content.variant_of, presentation_profile=content.presentation_profile.model_dump(),
        estimated_duration_sec=content.estimated_duration_sec or estimate_duration_sec(content), question_count=len(content.questions),
        content=data, content_hash=h, status="CONTENT_REVIEW", pool=pool, session_types=[],
    )  # fmt: skip
    session.add(unit)
    session.flush()
    return unit, "created"


def add_review_record(
    session, unit: m.Unit, *, target: str, method: str, reviewer: str, result: str, details: dict | None = None
) -> m.ReviewRecord:
    if method in DEMO_ONLY_METHODS and unit.realm != "demo":
        raise ValueError("mock review records are only valid in the demo realm")
    if target == "content" and method not in CONTENT_METHODS or target == "audio" and method not in AUDIO_METHODS:
        raise ValueError(f"method {method} is not valid for target {target}")
    rec = m.ReviewRecord(
        unit_id=unit.id,
        target=target,
        content_hash=unit.content_hash,
        method=method,
        reviewer=reviewer,
        result=result,
        details=details or {},
    )
    session.add(rec)
    session.flush()
    return rec


def passing_review(session, unit: m.Unit, target: str) -> m.ReviewRecord | None:
    """A record only counts for the exact content it was made for (same content_hash)."""
    return (
        session.execute(
            select(m.ReviewRecord)
            .where(
                m.ReviewRecord.unit_id == unit.id,
                m.ReviewRecord.target == target,
                m.ReviewRecord.content_hash == unit.content_hash,
                m.ReviewRecord.result == "pass",
            )
            .order_by(m.ReviewRecord.created_at.desc())
        )
        .scalars()
        .first()
    )


def carry_over_review(session, unit: m.Unit) -> m.ReviewRecord | None:
    """Reuse a content review made for identical content (same hash) on another version / realm copy."""
    rec = (
        session.execute(
            select(m.ReviewRecord)
            .join(m.Unit, m.Unit.id == m.ReviewRecord.unit_id)
            .where(
                m.ReviewRecord.target == "content",
                m.ReviewRecord.content_hash == unit.content_hash,
                m.ReviewRecord.result == "pass",
                m.Unit.realm == unit.realm,
            )
        )
        .scalars()
        .first()
    )
    if rec and rec.unit_id != unit.id:
        return add_review_record(
            session,
            unit,
            target="content",
            method=rec.method,
            reviewer=rec.reviewer,
            result="pass",
            details={"carried_from_unit": rec.unit_id},
        )
    return rec


def allowed_session_types(unit: m.Unit, roles: set[str]) -> list[str]:
    content = load_content(unit)
    has_answerable = all(f"stimulus:{s.id}" in roles for s in content.segments) and "feedback" in roles
    out: list[str] = []
    if unit.pool == "assessment":
        if has_answerable and "exam" in roles:
            out.append("assessment")
        return out
    if has_answerable:
        out += ["train", "review"]
    if "radio" in roles and (radio_supported(content)[0] or not has_answerable):
        out.append("radio")
    return out


def verify_assets_in_storage(session, unit: m.Unit) -> list[str]:
    st = get_storage()
    problems = []
    for a in session.execute(select(m.Asset).where(m.Asset.unit_id == unit.id)).scalars():
        if not st.exists(a.storage_key):
            problems.append(f"missing object for {a.role}")
            continue
        data = st.get(a.storage_key)
        if len(data) != a.bytes or sha256_hex(data) != a.sha256:
            problems.append(f"hash/size mismatch for {a.role}")
    return problems


def finalize_ready(session, unit: m.Unit, audio_review_state: str) -> None:
    """Objects were uploaded and verified first; this DB transaction only flips the state."""
    assets = session.execute(select(m.Asset).where(m.Asset.unit_id == unit.id)).scalars().all()
    roles = {a.role for a in assets}
    types = allowed_session_types(unit, roles)
    if not types:
        raise ValueError("no usable session type: required audio assets are missing")
    problems = verify_assets_in_storage(session, unit)
    if problems:
        raise ValueError("; ".join(problems))
    content = load_content(unit)
    seg_ms = {a.role.split(":", 1)[1]: a.duration_ms for a in assets if a.kind == "stimulus"}
    if seg_ms:
        unit.estimated_duration_sec = estimate_duration_sec(content, seg_ms)
    else:
        radio = next((a for a in assets if a.kind == "radio"), None)
        if radio:
            unit.estimated_duration_sec = radio.duration_ms // 1000
    unit.session_types = types
    unit.audio_review_state = audio_review_state
    unit.status = "READY"
    unit.status_reason = None
    unit.ready_at = datetime.now(UTC)
    # older READY versions of the same content stay in history but leave the candidate pool
    older = session.execute(
        select(m.Unit).where(
            m.Unit.realm == unit.realm,
            m.Unit.source == unit.source,
            m.Unit.content_id == unit.content_id,
            m.Unit.revision < unit.revision,
            m.Unit.superseded_by.is_(None),
        )
    ).scalars()
    for o in older:
        o.superseded_by = unit.id


def quarantine(session, unit: m.Unit, reason: str) -> None:
    unit.status = "QUARANTINED"
    unit.status_reason = reason[:2000]


def gc_orphan_objects(session, min_age_sec: int = 3600) -> list[str]:
    """Delete stored objects that no asset row references (left behind by failed uploads)."""
    import time

    st = get_storage()
    referenced = set(session.execute(select(m.Asset.storage_key)).scalars())
    removed = []
    for key, mtime in st.list("units"):
        if key not in referenced and time.time() - mtime > min_age_sec:
            st.delete(key)
            removed.append(key)
    for key, mtime in st.list("staging"):
        if time.time() - mtime > max(min_age_sec, 86400):
            st.delete(key)
            removed.append(key)
    return removed

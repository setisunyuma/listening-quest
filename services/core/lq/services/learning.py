"""Answer-type learning flow (train / review): start, plays, aids, submit, feedback, acknowledgement.

Everything that changes grades, observations, schedules, plans or XP happens here, inside one DB
transaction per request, keyed by client-supplied ids so retries return the same result."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from lq.db import models as m
from lq.db.base import new_id
from lq.domain import rules
from lq.domain.content import (
    MODES,
    SKILL_LABELS_JA,
    UnitContent,
    canonical_json,
    is_correct,
    learner_view,
    normalize_answer,
    sha256_hex,
)
from lq.services.content import load_content

DEFAULT_USER_SETTINGS = {
    "active_mode": "common_test",
    "daily_goal_minutes": 30,
    "timezone": "Asia/Tokyo",
    "day_cutoff_hour": 4,
    "playback_rate": 1.0,
    "sfx": False,  # sound effects are off by default
    "reduce_effects": False,
    "primary_side": "right",
    "font_scale": 1.0,
    "radio_include_new": True,
    "auto_offline": False,  # save the day's not-started units to the device whenever the app opens online
    "notify_reminder_hour": None,  # local hour for the daily reminder; None = no reminder
    "notify_new_material": True,
    "notify_problems": True,
    "exam_dates": {},  # mode -> "YYYY-MM-DD" (the learner's own target date; shown as days left)
}
ANSWER_ACCESS_KINDS = ("answer_access", "radio_access", "assessment")
ERROR_REASONS = ("sound", "vocabulary", "numbers_negation", "inference", "organizing", "focus", "other")


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Forbidden(Exception):
    pass


class Invalid(Exception):
    pass


def user_settings(user: m.User) -> dict:
    return {**DEFAULT_USER_SETTINGS, **(user.settings or {})}


def update_user_settings(user: m.User, patch: dict) -> dict:
    cur = user_settings(user)
    for k, v in patch.items():
        if k not in DEFAULT_USER_SETTINGS:
            continue
        if k == "active_mode" and v not in MODES:
            raise Invalid("unknown mode")
        if k == "daily_goal_minutes" and not (5 <= int(v) <= 180):
            raise Invalid("daily_goal_minutes must be 5..180")
        if k == "day_cutoff_hour" and not (0 <= int(v) <= 23):
            raise Invalid("day_cutoff_hour must be 0..23")
        if k == "timezone":
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise Invalid("unknown timezone") from e
        if k == "playback_rate" and not (0.5 <= float(v) <= 1.5):
            raise Invalid("playback_rate must be 0.5..1.5")
        if k == "notify_reminder_hour" and v is not None and int(v) == -1:
            v = None  # the settings API cannot send null, so -1 switches the reminder off
        if k == "notify_reminder_hour" and v is not None and not (0 <= int(v) <= 23):
            raise Invalid("notify_reminder_hour must be 0..23 or null")
        if k == "exam_dates":
            from datetime import date as _date

            if not isinstance(v, dict) or not set(v) <= set(MODES):
                raise Invalid("exam_dates must map modes to dates")
            clean = {}
            for mode_key, d in v.items():
                if d in (None, ""):
                    continue
                try:
                    clean[mode_key] = _date.fromisoformat(str(d)).isoformat()
                except ValueError as e:
                    raise Invalid("exam date must be YYYY-MM-DD") from e
            v = clean
        if k == "primary_side" and v not in ("left", "right"):
            raise Invalid("primary_side must be left or right")
        cur[k] = v
    user.settings = cur
    return cur


def user_day(user: m.User, now: datetime) -> date:
    s = user_settings(user)
    return rules.learning_day(now, s["timezone"], int(s["day_cutoff_hour"]))


# ---------------------------------------------------------------- idempotent client events


def claim_event(session, user_id: str, event_id: str, kind: str, payload: dict) -> dict | None:
    """Returns the stored response if this event was already processed, else None (and reserves the id).
    The same id with a different payload is a conflict."""
    h = sha256_hex(canonical_json(payload))
    try:
        with session.begin_nested():
            session.add(m.ClientEvent(id=event_id, user_id=user_id, kind=kind, payload_hash=h, response={}))
        return None
    except IntegrityError:
        ev = session.get(m.ClientEvent, event_id)
        if ev is None or ev.user_id != user_id or ev.kind != kind or ev.payload_hash != h:
            raise Conflict("event id was already used with different content") from None
        return ev.response or {"duplicate": True}


def finish_event(session, event_id: str, response: dict) -> dict:
    session.get(m.ClientEvent, event_id).response = response
    return response


# ---------------------------------------------------------------- helpers


def get_unit_for(session, user: m.User, unit_id: str) -> m.Unit:
    unit = session.get(m.Unit, unit_id)
    if unit is None or unit.realm != user.realm:
        raise NotFound("unit not found")
    return unit


def lineage_unit_ids(session, unit: m.Unit) -> list[str]:
    return list(
        session.execute(
            select(m.Unit.id).where(
                m.Unit.realm == unit.realm, m.Unit.source == unit.source, m.Unit.content_id == unit.content_id
            )
        ).scalars()
    )


def prior_exposures(
    session,
    user_id: str,
    unit: m.Unit,
    before: datetime | None = None,
    exclude_attempt: str | None = None,
    kinds: tuple[str, ...] | None = None,
    after: datetime | None = None,
) -> list[m.ExposureEvent]:
    E = m.ExposureEvent
    q = select(E).where(
        E.user_id == user_id,
        or_(E.stimulus_fingerprint == unit.stimulus_fingerprint, E.unit_id.in_(lineage_unit_ids(session, unit))),
    )
    if before is not None:
        q = q.where(E.at < before)
    if after is not None:
        q = q.where(E.at >= after)
    if exclude_attempt:
        q = q.where(or_(E.attempt_id.is_(None), E.attempt_id != exclude_attempt))
    if kinds:
        q = q.where(E.kind.in_(kinds))
    return list(session.execute(q).scalars())


def add_exposure(
    session,
    user_id: str,
    unit: m.Unit,
    kind: str,
    *,
    attempt_id: str | None = None,
    session_id: str | None = None,
    meta: dict | None = None,
    event_id: str | None = None,
    at: datetime | None = None,
) -> None:
    values = dict(
        user_id=user_id,
        unit_id=unit.id,
        stimulus_fingerprint=unit.stimulus_fingerprint,
        family_id=unit.family_id,
        kind=kind,
        attempt_id=attempt_id,
        session_id=session_id,
        meta=meta or {},
        at=at or datetime.now(UTC),
    )
    values["id"] = event_id or new_id()
    session.execute(pg_insert(m.ExposureEvent).values(**values).on_conflict_do_nothing(index_elements=["id"]))


def get_mode_state(session, user_id: str, mode: str, lock: bool = False) -> m.ModeState:
    q = select(m.ModeState).where(m.ModeState.user_id == user_id, m.ModeState.mode == mode)
    st = session.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if st is None:
        session.execute(
            pg_insert(m.ModeState)
            .values(
                id=sha256_hex(f"{user_id}:{mode}")[:32],
                user_id=user_id,
                mode=mode,
                difficulty_band=rules.load_config().initial_band,
                band_since=datetime(2000, 1, 1, tzinfo=UTC),
                band_history=[],
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
        )
        st = session.execute(q.with_for_update() if lock else q).scalar_one()
    return st


def get_or_create_session(
    session, user_id: str, session_type: str, mode: str, plan_id: str | None, day: date
) -> m.LearningSession:
    S = m.LearningSession
    q = select(S).where(
        S.user_id == user_id,
        S.session_type == session_type,
        S.mode == mode,
        S.status == "open",
        S.meta["day"].astext == day.isoformat(),
    )
    q = q.where(S.plan_id == plan_id) if plan_id else q.where(S.plan_id.is_(None))
    row = session.execute(q.limit(1)).scalar_one_or_none()
    if row is None:
        row = m.LearningSession(
            user_id=user_id, session_type=session_type, mode=mode, plan_id=plan_id, meta={"day": day.isoformat()}
        )
        session.add(row)
        session.flush()
    return row


def unit_assets(session, unit_id: str) -> dict[str, m.Asset]:
    return {a.role: a for a in session.execute(select(m.Asset).where(m.Asset.unit_id == unit_id)).scalars()}


def audio_url(asset: m.Asset) -> str:
    return f"/api/audio/{asset.id}"


def allowed_plays(content: UnitContent) -> dict[str, int]:
    return {s.id: s.plays for s in content.segments}


# ---------------------------------------------------------------- start


def start_attempt(
    session,
    user: m.User,
    *,
    attempt_id: str,
    unit_id: str,
    slot_id: str | None = None,
    now: datetime | None = None,
    offline: bool = False,
    started_at: datetime | None = None,
) -> m.Attempt:
    now = now or datetime.now(UTC)
    existing = session.get(m.Attempt, attempt_id)
    if existing:
        if existing.user_id != user.id or existing.unit_id != unit_id:
            raise Conflict("attempt id already used for something else")
        return existing
    # one start at a time per user: two tabs opening the same unit cannot both become "first"
    session.execute(select(m.User.id).where(m.User.id == user.id).with_for_update())
    unit = get_unit_for(session, user, unit_id)
    if unit.status != "READY" or unit.invalidated_at is not None:
        raise Invalid("this unit is not available")
    if "train" not in unit.session_types:
        raise Invalid("this unit cannot be used for answering (radio only)")
    A = m.Attempt
    q = select(A).where(
        A.user_id == user.id, A.unit_id == unit_id, A.status == "open", A.session_type.in_(("train", "review"))
    )
    q = q.where(A.slot_id == slot_id) if slot_id else q.where(A.slot_id.is_(None))
    open_one = session.execute(q.limit(1)).scalar_one_or_none()
    if open_one is not None:  # reload / second tab: resume the same attempt instead of creating another
        return open_one

    slot = plan = None
    if slot_id:
        slot = session.execute(
            select(m.PlanSlot).where(m.PlanSlot.id == slot_id).with_for_update()
        ).scalar_one_or_none()
        plan = session.get(m.DailyPlan, slot.plan_id) if slot else None
        if slot is None or plan is None or plan.user_id != user.id:
            raise NotFound("plan slot not found")
        if slot.unit_id != unit_id:
            raise Conflict("this slot is now assigned to a different unit")
        if slot.state in ("completed", "removed"):
            raise Conflict("this slot is already finished")

    start = started_at or now
    prior = prior_exposures(session, user.id, unit, before=start)
    eligible = rules.initial_first_eligible(e.kind for e in prior)
    E = m.ExposureEvent
    family_seen = (
        session.execute(
            select(func.count())
            .select_from(E)
            .where(
                E.user_id == user.id,
                E.family_id == unit.family_id,
                E.stimulus_fingerprint != unit.stimulus_fingerprint,
                E.at < start,
            )
        ).scalar_one()
        > 0
    )
    today = plan.learning_day if plan else user_day(user, now)
    sched = session.execute(
        select(m.ReviewSchedule).where(
            m.ReviewSchedule.user_id == user.id, m.ReviewSchedule.unit_id == unit.id, m.ReviewSchedule.kind == "recall"
        )
    ).scalar_one_or_none()
    due = sched is not None and sched.due_day <= user_day(user, now)
    session_type = "review" if due else "train"
    state = get_mode_state(session, user.id, unit.mode)
    sess = get_or_create_session(session, user.id, session_type, unit.mode, plan.id if plan else None, today)
    att = m.Attempt(
        id=attempt_id, user_id=user.id, unit_id=unit.id, stimulus_fingerprint=unit.stimulus_fingerprint, mode=unit.mode, session_id=sess.id,
        session_type=session_type, slot_id=slot_id, presentation="official" if unit.presentation_profile.get("kind") == "official" else "arranged",
        eligible_at_start=eligible, family_seen_before=family_seen, review_was_due=due, band_at_start=state.difficulty_band, plays={}, aids={},
        offline=offline, learning_day=today, started_at=start, first_claim=False,
    )  # fmt: skip
    session.add(att)
    session.flush()
    if eligible and session_type == "train":
        try:
            with session.begin_nested():  # unique partial index: only one "first" per user + stimulus, ever
                att.first_claim = True
                session.flush()
        except IntegrityError:
            session.refresh(att)
            att.first_claim = False
    if slot is not None:
        slot.state = "started"
        slot.attempt_id = att.id
    return att


def attempt_dto(session, att: m.Attempt, *, show_audio_prompts: bool | None = None) -> dict:
    unit = session.get(m.Unit, att.unit_id)
    content = load_content(unit)
    assets = unit_assets(session, unit.id)
    if show_audio_prompts is None:
        show_audio_prompts = bool(att.aids.get("prompt_text"))
    view = learner_view(content, show_audio_prompts=show_audio_prompts)
    for seg in view["segments"]:
        a = assets.get(f"stimulus:{seg['id']}")
        seg["audio"] = {"url": audio_url(a), "duration_ms": a.duration_ms, "bytes": a.bytes} if a else None
    return {
        "attempt_id": att.id,
        "unit_id": unit.id,
        "unit_revision": unit.revision,
        "status": att.status,
        "session_type": att.session_type,
        "presentation": att.presentation,
        "counts_as_first": att.first_claim,
        "slot_id": att.slot_id,
        "plays": att.plays,
        "aids": att.aids,
        "audio_kind": unit.audio_kind,
        "band": unit.difficulty_band,
        "unit": view,
    }


def _open_attempt(session, user: m.User, attempt_id: str, lock: bool = True) -> m.Attempt:
    q = select(m.Attempt).where(m.Attempt.id == attempt_id)
    att = session.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if att is None or att.user_id != user.id:
        raise NotFound("attempt not found")
    return att


# ---------------------------------------------------------------- plays and aids


def record_play(
    session,
    user: m.User,
    attempt_id: str,
    *,
    event_id: str,
    segment_id: str,
    rate: float = 1.0,
    at: datetime | None = None,
) -> dict:
    payload = {"attempt": attempt_id, "segment": segment_id, "rate": round(float(rate), 2)}
    done = claim_event(session, user.id, event_id, "play", payload)
    if done is not None:
        return done
    att = _open_attempt(session, user, attempt_id)
    unit = session.get(m.Unit, att.unit_id)
    content = load_content(unit)
    allowed = allowed_plays(content)
    if segment_id not in allowed:
        raise Invalid("unknown segment")
    if att.status == "open":  # plays after submission are feedback-time replays and change nothing
        att.plays = {**att.plays, segment_id: att.plays.get(segment_id, 0) + 1}
        aids = dict(att.aids)
        if att.plays[segment_id] > allowed[segment_id]:
            aids["extra_replay"] = True
        if abs(float(rate) - 1.0) > 0.01:
            aids["speed"] = True
        att.aids = aids
        flag_modified(att, "plays")
        flag_modified(att, "aids")
    add_exposure(
        session,
        user.id,
        unit,
        "stimulus_play",
        attempt_id=att.id,
        session_id=att.session_id,
        meta={"segment": segment_id, "rate": rate},
        event_id=event_id,
        at=at,
    )
    return finish_event(session, event_id, {"plays": att.plays, "aids": att.aids, "allowed_plays": allowed})


def request_aid(session, user: m.User, attempt_id: str, *, event_id: str, kind: str) -> dict:
    """Explicit help before answering. The use is recorded first; only the script / hint / prompt text is
    returned - never answers or feedback."""
    if kind not in ("transcript", "hint", "prompt_text"):
        raise Invalid("unknown aid")
    att = _open_attempt(session, user, attempt_id)
    if att.session_type not in ("train", "review"):
        raise Forbidden("aids are not available in this session type")
    unit = session.get(m.Unit, att.unit_id)
    content = load_content(unit)
    if att.status == "open" and not att.aids.get(kind):
        att.aids = {**att.aids, kind: True}
        flag_modified(att, "aids")
        add_exposure(session, user.id, unit, kind, attempt_id=att.id, session_id=att.session_id, event_id=event_id)
        session.flush()
    if kind == "transcript":
        labels = {s.id: (s.label or s.id) for s in content.speakers}
        data = {
            "segments": [
                {
                    "id": seg.id,
                    "lines": [
                        {"utterance_id": u.id, "speaker": labels.get(u.speaker_id, u.speaker_id), "text": u.text}
                        for u in seg.utterances
                    ],
                }
                for seg in content.segments
            ]
        }
    elif kind == "hint":
        data = {"hints": {q.id: q.feedback.hint_ja for q in content.questions if q.feedback.hint_ja}}
    else:
        data = {"prompts": {q.id: q.prompt_en for q in content.questions}}
    return {"kind": kind, "aids": att.aids, **data}


# ---------------------------------------------------------------- submit


def _srs_outcome(complete: bool, all_correct: bool, assisted: bool) -> str:
    if not complete or not all_correct:
        return "incorrect"
    return "correct_assisted" if assisted else "correct_unaided"


def apply_schedule(
    session,
    user: m.User,
    unit: m.Unit,
    outcome: str,
    today: date,
    *,
    attempt_id: str | None,
    kind: str = "recall",
    source: str = "attempt",
) -> dict:
    cfg = rules.load_config()
    R = m.ReviewSchedule
    row = session.execute(
        select(R).where(R.user_id == user.id, R.unit_id == unit.id, R.kind == kind).with_for_update()
    ).scalar_one_or_none()
    cur = rules.Sched(row.stage, row.due_day) if row else None
    res = rules.srs_next(cur, outcome, today, cfg)
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "day": today.isoformat(),
        "from_stage": cur.stage if cur else None,
        "to_stage": res.sched.stage,
        "due_day": res.sched.due_day.isoformat(),
        "reason_code": res.reason_code,
        "reason_ja": res.reason_ja,
        "outcome": outcome,
        "attempt_id": attempt_id,
        "source": source,
        "changed": res.changed,
    }
    if row is None:
        row = R(
            user_id=user.id,
            unit_id=unit.id,
            kind=kind,
            stage=res.sched.stage,
            due_day=res.sched.due_day,
            last_result=outcome,
            history=[entry],
        )
        session.add(row)
    else:
        row.stage, row.due_day = res.sched.stage, res.sched.due_day
        row.last_result = outcome
        row.history = [*row.history, entry][-50:]
        row.updated_at = datetime.now(UTC)
    return {
        "stage": res.sched.stage,
        "due_day": res.sched.due_day.isoformat(),
        "reason_code": res.reason_code,
        "reason_ja": res.reason_ja,
        "interval_days": cfg.review_intervals_days[res.sched.stage],
    }


def is_unassisted_review(att: m.Attempt) -> bool:
    """R04: one definition of "retained without help", shared by growth, home and the library. An assisted
    review stays in the learning record (and in the assisted figures) but never counts as retention."""
    return att.category == "review" and bool(att.complete) and not (att.result or {}).get("assisted")


def band_observations(session, user_id: str, mode: str, since: datetime) -> list[tuple[str, float]]:
    """Evidence for moving the difficulty band (R04): first, unassisted, complete attempts on valid material
    whose OWN band equals the learner's band at the start of the attempt, in the official presentation.
    Easier or harder material can still be practised; it just does not move the band."""
    A, U = m.Attempt, m.Unit
    rows = session.execute(
        select(A.stimulus_fingerprint, A.unit_score)
        .join(U, U.id == A.unit_id)
        .where(
            A.user_id == user_id,
            A.mode == mode,
            A.strict_first.is_(True),
            A.complete.is_(True),
            A.status == "submitted",
            U.invalidated_at.is_(None),
            A.submitted_at > since,
            U.difficulty_band == A.band_at_start,
            A.presentation == "official",
        )
        .order_by(A.submitted_at)
    ).all()
    return [(fp, float(score)) for fp, score in rows]


def update_band(session, user: m.User, mode: str, today: date, now: datetime) -> dict:
    cfg = rules.load_config()
    st = get_mode_state(session, user.id, mode, lock=True)
    obs = band_observations(session, user.id, mode, st.band_since)
    d = rules.adjust_band(st.difficulty_band, obs, st.last_band_change_day, today, cfg)
    if d.changed:
        st.band_history = [
            *st.band_history,
            {
                "at": now.isoformat(),
                "day": today.isoformat(),
                "from": st.difficulty_band,
                "to": d.band,
                "reason": d.reason,
                "n": d.n,
                "mean": round(d.mean, 4),
            },
        ]
        st.difficulty_band = d.band
        st.band_since = now
        st.last_band_change_day = today
        st.updated_at = now
    return {"band": d.band, "changed": d.changed, "reason": d.reason, "n": d.n, "mean": d.mean}


def submit_attempt(
    session,
    user: m.User,
    attempt_id: str,
    answers: dict,
    *,
    now: datetime | None = None,
    client_meta: dict | None = None,
    plays: list[dict] | None = None,
) -> m.Attempt:
    """Grade and commit every consequence exactly once. `answers` must contain every question id; the
    value is an answer object or null for an explicit skip. `plays` are the playback events the device kept
    (R03): any not yet received are applied BEFORE grading, so a replay or speed change whose own request was
    lost still marks the attempt as assisted. Events already received are skipped by their ids."""
    now = now or datetime.now(UTC)
    att = _open_attempt(session, user, attempt_id)
    if plays and att.status == "open":
        for p in plays:
            try:
                record_play(
                    session,
                    user,
                    attempt_id,
                    event_id=p["event_id"],
                    segment_id=p["segment_id"],
                    rate=float(p.get("rate", 1.0)),
                )
            except Conflict:
                pass  # the same id was used for something else: never let it change this attempt
        att = _open_attempt(session, user, attempt_id)
    unit = session.get(m.Unit, att.unit_id)
    content = load_content(unit)
    qmap = content.question_map()
    if set(answers) != set(qmap):
        raise Invalid("answers must contain every question of the unit (use null for an explicit skip)")
    try:
        normalized = {qid: normalize_answer(qmap[qid], answers[qid]) for qid in sorted(qmap)}
    except ValueError as e:
        raise Invalid(str(e)) from e
    h = sha256_hex(canonical_json(normalized))
    if att.status == "submitted":
        if att.answer_hash == h:
            return att  # same submission again: same result, no side effects
        raise Conflict("this attempt was already submitted with different answers")
    if att.session_type == "assessment":
        raise Forbidden("assessment answers are submitted with the whole exam")

    per_q, skills_wrong, skills_right = {}, [], []
    for q in content.sorted_questions():
        ans = normalized[q.id]
        ok = is_correct(q, ans)
        per_q[q.id] = {
            "answer": ans,
            "correct": ok,
            "skipped": ans is None,
            "system_guess": None if ok else q.skill_tags[0],
        }
        for s in q.skill_tags:
            (skills_right if ok else skills_wrong).append(s)
    complete = all(v["answer"] is not None for v in per_q.values())
    correct_n = sum(1 for v in per_q.values() if v["correct"])
    all_correct = correct_n == len(per_q)
    allowed = allowed_plays(content)
    assisted = rules.is_assisted(att.plays, allowed, att.aids)
    external = bool(
        prior_exposures(session, user.id, unit, exclude_attempt=att.id, kinds=ANSWER_ACCESS_KINDS, after=att.started_at)
    )
    strict, reasons = rules.judge_strict_first(
        rules.AttemptFacts(
            session_type=att.session_type,
            eligible_at_start=att.eligible_at_start,
            first_claim=att.first_claim,
            plays=att.plays,
            allowed_plays=allowed,
            aids=att.aids,
            external_answer_access=external,
            complete=complete,
            invalidated=unit.invalidated_at is not None,
        )  # fmt: skip
    )
    if not complete:
        category = "partial"
    elif strict:
        category = "first"
    elif att.review_was_due:
        category = "review"
    elif assisted and att.eligible_at_start and att.first_claim and not external:
        category = "assisted"
    else:
        category = "repeat"

    att.answers = normalized
    att.answer_hash = h
    att.status = "submitted"
    att.submitted_at = now
    att.complete = complete
    att.unit_score = correct_n / len(per_q) if complete else None
    att.strict_first = strict
    att.not_first_reasons = reasons
    att.category = category
    att.client_meta = {**(att.client_meta or {}), **(client_meta or {})}
    att.result = {"questions": per_q, "correct_count": correct_n, "total": len(per_q), "complete": complete, "assisted": assisted,
                  "skills_wrong": sorted(set(skills_wrong)), "skills_right": sorted(set(skills_right) - set(skills_wrong))}  # fmt: skip

    # observations: one per skill per unit (mean over that unit's questions), strict-first only
    if strict:
        by_skill: dict[str, list[float]] = {}
        for q in content.questions:
            for s in q.skill_tags:
                by_skill.setdefault(s, []).append(1.0 if per_q[q.id]["correct"] else 0.0)
        for s, vals in by_skill.items():
            session.add(
                m.SkillObservation(
                    user_id=user.id,
                    mode=unit.mode,
                    unit_id=unit.id,
                    attempt_id=att.id,
                    skill=s,
                    kind="strict_first",
                    presentation=att.presentation,
                    score=sum(vals) / len(vals),
                    band=unit.difficulty_band,
                )
            )
    need = set(skills_wrong) | ({unit.primary_skill} if assisted else set())
    for s in sorted(need):  # "missed or needed help just now": steers the next pick, never the band
        session.add(
            m.SkillObservation(
                user_id=user.id,
                mode=unit.mode,
                unit_id=unit.id,
                attempt_id=att.id,
                skill=s,
                kind="need_signal",
                presentation=att.presentation,
                score=0.0,
                band=unit.difficulty_band,
            )
        )

    add_exposure(
        session,
        user.id,
        unit,
        "answer_access",
        attempt_id=att.id,
        session_id=att.session_id,
        meta={"via": "feedback"},
        at=now,
    )
    today = user_day(user, now)
    sched = apply_schedule(session, user, unit, _srs_outcome(complete, all_correct, assisted), today, attempt_id=att.id)
    band = update_band(session, user, unit.mode, today, now) if strict else None
    att.result = {**att.result, "schedule": sched, "band": band}
    session.flush()
    if att.slot_id:
        from lq.services import plans

        plans.after_unit(session, user, att.slot_id, now)
    return att


# ---------------------------------------------------------------- feedback (only after submission)


def result_dto(session, att: m.Attempt) -> dict:
    if att.status != "submitted":
        raise Forbidden("answers and explanations unlock after every question is answered or skipped")
    unit = session.get(m.Unit, att.unit_id)
    content = load_content(unit)
    assets = unit_assets(session, unit.id)
    labels = {s.id: (s.label or s.id) for s in content.speakers}
    utt = content.utterance_map()
    questions = []
    for q in content.sorted_questions():
        r = att.result["questions"][q.id]
        ev = []
        for e in q.evidence:
            if e.utterance_id:
                a = assets.get(f"utt:{e.utterance_id}")
                u = utt[e.utterance_id]
                ev.append(
                    {
                        "type": "utterance",
                        "utterance_id": u.id,
                        "speaker": labels.get(u.speaker_id, u.speaker_id),
                        "text": u.text,
                        "audio_url": audio_url(a) if a else None,
                    }
                )
            else:
                ev.append({"type": "figure", "figure_id": e.figure_id, "element_id": e.element_id})
        questions.append({
            "id": q.id, "slot": q.slot, "type": q.type, "prompt_en": q.prompt_en, "choices": [c.model_dump() for c in q.choices], "items": [i.model_dump() for i in q.items],
            "your_answer": r["answer"], "skipped": r["skipped"], "correct": r["correct"], "correct_answer": q.answer.model_dump(), "answer_en": q.feedback.answer_en,
            "explanation_ja": q.feedback.explanation_ja, "distractors": {d.choice_id: d.reason_ja for d in q.feedback.distractors}, "evidence": ev,
            "skill_tags": q.skill_tags, "system_guess": r.get("system_guess"), "system_guess_label": SKILL_LABELS_JA.get(r.get("system_guess") or "", None),
            "self_reason": (att.error_reasons or {}).get(q.id),
        })  # fmt: skip
    fb = assets.get("feedback")
    ja = {t.utterance_id: t.ja for t in content.study_notes.translations}
    return {
        "attempt_id": att.id, "unit_id": unit.id, "status": att.status, "session_type": att.session_type, "category": att.category,
        "complete": att.complete, "correct_count": att.result["correct_count"], "total": att.result["total"],
        "counted_as_first": att.strict_first, "not_first_reasons": att.not_first_reasons, "assisted": att.result.get("assisted", False),
        "presentation": att.presentation, "schedule": att.result.get("schedule"), "band": att.result.get("band"),
        "feedback_audio": {"url": audio_url(fb), "duration_ms": fb.duration_ms} if fb else None,
        "transcript": [{"id": seg.id, "lines": [{"utterance_id": u.id, "speaker": labels.get(u.speaker_id, u.speaker_id), "text": u.text,
                                                "audio_url": audio_url(assets[f"utt:{u.id}"]) if f"utt:{u.id}" in assets else None,
                                                "ja": ja.get(u.id)} for u in seg.utterances]} for seg in content.segments],
        "glossary": [g.model_dump() for g in content.study_notes.glossary],
        "figures": [f.model_dump() for f in content.figures], "questions": questions, "feedback_viewed": att.feedback_viewed_at is not None,
        "slot_id": att.slot_id, "audio_kind": unit.audio_kind, "title": unit.title,
    }  # fmt: skip


def award_xp(session, user_id: str, kind: str, unit_id: str | None, day: date, amount: int, dedupe_key: str) -> int:
    res = session.execute(
        pg_insert(m.XpEvent)
        .values(
            id=sha256_hex(dedupe_key)[:32],
            user_id=user_id,
            kind=kind,
            unit_id=unit_id,
            learning_day=day,
            amount=amount,
            dedupe_key=dedupe_key,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing()
        .returning(m.XpEvent.id)
    )
    return amount if res.first() is not None else 0


def total_xp(session, user_id: str) -> int:
    return int(
        session.execute(
            select(func.coalesce(func.sum(m.XpEvent.amount), 0)).where(m.XpEvent.user_id == user_id)
        ).scalar_one()
    )


def ack_feedback(session, user: m.User, attempt_id: str, *, now: datetime | None = None) -> dict:
    """The learner has looked at the explanation. Completion and base XP are confirmed here - and only if
    the graded answers already exist, so a reordered event can never pay out before the answer."""
    now = now or datetime.now(UTC)
    att = _open_attempt(session, user, attempt_id)
    if att.status != "submitted":
        raise Conflict("answers have not been submitted yet")
    cfg = rules.load_config()
    awarded = []
    if att.feedback_viewed_at is None:
        att.feedback_viewed_at = now
    plan_info = None
    if att.complete and att.session_type in ("train", "review"):
        got = award_xp(
            session,
            user.id,
            "unit_base",
            att.unit_id,
            att.learning_day,
            cfg.xp["unit_base"],
            f"unit_base:{user.id}:{att.unit_id}:{att.learning_day.isoformat()}",
        )
        if got:
            awarded.append({"kind": "unit_base", "amount": got})
        if att.review_was_due:
            got = award_xp(
                session,
                user.id,
                "review_bonus",
                att.unit_id,
                att.learning_day,
                cfg.xp["review_bonus"],
                f"review_bonus:{user.id}:{att.unit_id}:{att.learning_day.isoformat()}",
            )
            if got:
                awarded.append({"kind": "review_bonus", "amount": got})
        if att.slot_id:
            from lq.services import plans

            plan_info = plans.complete_slot(session, user, att.slot_id, att.id, now)
            if plan_info and plan_info.get("daily_xp"):
                awarded.append({"kind": "daily_plan", "amount": plan_info["daily_xp"]})
    xp = total_xp(session, user.id)
    return {
        "awarded": awarded,
        "total_xp": xp,
        "level": rules.level_for(xp, cfg),
        "slot_completed": bool(plan_info and plan_info.get("slot_completed")),
        "plan_completed": bool(plan_info and plan_info.get("plan_completed")),
        "needs_all_answers": not att.complete,
    }


def set_error_reason(session, user: m.User, attempt_id: str, question_id: str, reason: str | None) -> dict:
    """Self-declared reason for a miss. Stored apart from the system's guess; never changes grading."""
    if reason is not None and reason not in ERROR_REASONS:
        raise Invalid("unknown reason")
    att = _open_attempt(session, user, attempt_id)
    if att.status != "submitted" or question_id not in (att.result or {}).get("questions", {}):
        raise Invalid("question not found in a submitted attempt")
    er = dict(att.error_reasons or {})
    if reason is None:
        er.pop(question_id, None)
    else:
        er[question_id] = reason
    att.error_reasons = er
    return er


def recent_window(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)

"""FastAPI application. Authentication, grading, plans, recommendations and every DB update live here;
the web app holds no business rules of its own."""

from __future__ import annotations

import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from lq.api import schemas as S
from lq.config import get_settings
from lq.db import models as m
from lq.db.base import new_id, session_factory
from lq.domain.content import MODE_LABELS_JA, MODES, SKILL_LABELS_JA, SKILLS, unit_json_schema
from lq.domain.profiles import current_profile, load_profiles
from lq.generator import budget
from lq.jobs import queue
from lq.services import (
    admin,
    assessment,
    auth,
    backup,
    diagnostics,
    feedback,
    importer,
    notify,
    plans,
    radio,
    replenish,
    stats,
)
from lq.services import learning as L
from lq.storage import get_storage

app = FastAPI(title="Listening Quest API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_ID = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


def now_utc() -> datetime:
    return datetime.now(UTC)


def db() -> Session:
    s = session_factory()()
    try:
        yield s
    finally:
        s.close()


@app.exception_handler(L.NotFound)
async def _nf(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(L.Conflict)
async def _cf(_r, e):
    return JSONResponse({"detail": str(e), "code": type(e).__name__}, status_code=409)


@app.exception_handler(L.Forbidden)
async def _fb(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=403)


@app.exception_handler(L.Invalid)
async def _iv(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=422)


def current_user(
    request: Request, session: Session = Depends(db), x_csrf_token: str | None = Header(default=None)
) -> m.User:
    """Cookie session + CSRF (double submit) on every state-changing request."""
    got = auth.resolve(session, request.cookies.get(auth.SESSION_COOKIE))
    if got is None:
        raise HTTPException(401, "login required")
    user, sess = got
    if request.method in _UNSAFE:
        if not x_csrf_token or not hmac.compare_digest(x_csrf_token, sess.csrf_token):
            raise HTTPException(403, "CSRF token missing or wrong")
        origin = get_settings().public_origin
        if origin and request.headers.get("origin") not in (None, origin):
            raise HTTPException(403, "cross-origin request refused")
    request.state.csrf = sess.csrf_token
    return user


def _set_cookies(resp: Response, token: str, csrf: str) -> None:
    s = get_settings()
    age = s.session_ttl_hours * 3600
    resp.set_cookie(
        auth.SESSION_COOKIE, token, max_age=age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/"
    )
    resp.set_cookie(
        auth.CSRF_COOKIE, csrf, max_age=age, httponly=False, secure=s.cookie_secure, samesite="lax", path="/"
    )


# ---------------------------------------------------------------- auth / settings


@app.get("/api/health")
def health(session: Session = Depends(db)) -> dict[str, Any]:
    session.execute(select(1))
    return {"ok": True}


def _setup_state(session: Session) -> tuple[bool, str]:
    code = get_settings().setup_code
    if session.execute(select(m.User.id).where(m.User.realm == "live").limit(1)).first():
        return False, "account_exists"
    if not code or len(code) < 12:
        return False, "setup_code_not_configured"
    return True, "ready"


@app.get("/api/auth/setup", response_model=S.SetupStatusOut)
def setup_status(session: Session = Depends(db)):
    ok, reason = _setup_state(session)
    return {"available": ok, "reason": reason}


@app.post("/api/auth/setup", response_model=S.MeOut)
def setup_owner(body: S.SetupIn, request: Request, response: Response, session: Session = Depends(db)):
    """Create the single owner account from the browser, once. Requires LQ_SETUP_CODE (chosen by the owner in
    the hosting dashboard). Wrong codes count as failed logins, so guessing is rate-limited."""
    client = (
        (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?"))
        .split(",")[0]
        .strip()
    )
    session.execute(text("SELECT pg_advisory_xact_lock(4242001)"))  # serialise concurrent setups
    ok, reason = _setup_state(session)
    if not ok:
        raise HTTPException(409 if reason == "account_exists" else 404, "setup is not available")
    since = now_utc() - timedelta(minutes=get_settings().login_window_minutes)
    key = f"setup:{client}"
    fails = session.execute(
        select(func.count())
        .select_from(m.LoginAttempt)
        .where(m.LoginAttempt.key == key, m.LoginAttempt.success.is_(False), m.LoginAttempt.at >= since)
    ).scalar_one()
    if fails >= get_settings().login_max_failures:
        raise HTTPException(429, "too many attempts; wait and try again")
    if not hmac.compare_digest(body.code.encode(), get_settings().setup_code.encode()):
        session.add(m.LoginAttempt(key=key, success=False, at=now_utc()))
        session.commit()
        raise HTTPException(403, "wrong setup code")
    try:
        auth.create_user(session, body.username, body.password, realm="live")
    except ValueError as e:
        raise L.Invalid(str(e)) from e
    session.add(
        m.AuditLog(
            realm="live", actor=body.username, action="owner_created_via_setup", target=body.username, details={}
        )
    )
    user, token, csrf = auth.login(session, body.username, body.password, client)
    session.commit()
    _set_cookies(response, token, csrf)
    return {
        "username": user.username,
        "realm": user.realm,
        "user_id": user.id,
        "csrf_token": csrf,
        "settings": L.user_settings(user),
    }


@app.post("/api/auth/login", response_model=S.MeOut)
def login(body: S.LoginIn, request: Request, response: Response, session: Session = Depends(db)):
    client = (
        (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?"))
        .split(",")[0]
        .strip()
    )
    try:
        user, token, csrf = auth.login(session, body.username, body.password, client)
    except auth.TooManyAttempts:
        session.rollback()
        raise HTTPException(429, "too many failed logins; wait and try again") from None
    session.commit()
    if user is None:
        raise HTTPException(401, "wrong username or password")
    _set_cookies(response, token, csrf)
    return {
        "username": user.username,
        "realm": user.realm,
        "user_id": user.id,
        "csrf_token": csrf,
        "settings": L.user_settings(user),
    }


@app.post("/api/auth/logout")
def logout(
    request: Request, response: Response, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    auth.logout(session, request.cookies.get(auth.SESSION_COOKIE))
    session.commit()
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    response.delete_cookie(auth.CSRF_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me", response_model=S.MeOut)
def me(request: Request, user: m.User = Depends(current_user)):
    return {
        "username": user.username,
        "realm": user.realm,
        "user_id": user.id,
        "csrf_token": request.state.csrf,
        "settings": L.user_settings(user),
    }


@app.get("/api/push/key")
def push_key(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    from lq import push

    key = push.public_key_b64(session)
    session.commit()  # the key pair is created on first use
    n = session.execute(
        select(func.count()).select_from(m.PushSubscription).where(m.PushSubscription.user_id == user.id)
    ).scalar_one()
    return {"public_key": key, "devices": int(n)}


@app.post("/api/push/subscriptions")
def push_subscribe(
    body: S.PushSubscribeIn, request: Request, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    if not body.endpoint.startswith("https://"):
        raise L.Invalid("push endpoint must be https")
    sub = session.execute(
        select(m.PushSubscription).where(m.PushSubscription.endpoint == body.endpoint)
    ).scalar_one_or_none()
    if sub is None:
        sub = m.PushSubscription(endpoint=body.endpoint, user_id=user.id, p256dh=body.p256dh, auth=body.auth)
        session.add(sub)
    sub.user_id, sub.p256dh, sub.auth = user.id, body.p256dh, body.auth
    sub.user_agent = (request.headers.get("user-agent") or "")[:300]
    session.commit()
    return {"subscribed": True}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(
    body: S.PushSubscribeIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    session.execute(
        delete(m.PushSubscription).where(
            m.PushSubscription.endpoint == body.endpoint, m.PushSubscription.user_id == user.id
        )
    )
    session.commit()
    return {"subscribed": False}


@app.post("/api/push/test")
def push_test(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    out = notify.send_all(
        session,
        user,
        {"title": "Listening Quest", "body": "通知のテストです。届いていれば設定は完了です。", "url": "/settings"},
    )
    session.commit()
    return out


@app.patch("/api/settings")
def patch_settings(
    body: S.SettingsPatch, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = L.update_user_settings(user, body.model_dump(exclude_none=True))
    session.commit()
    return out


@app.get("/api/meta")
def meta(user: m.User = Depends(current_user)) -> dict[str, Any]:
    profs = []
    for mode in MODES:
        p = current_profile(mode)
        profs.append({"mode": mode, "label": MODE_LABELS_JA[mode], "profile_id": p["id"], "profile_label": p["label_ja"], "kind": p["kind"], "exam": p["exam"],
                      "parts": [{"part": x["part"], "label_ja": x["label_ja"], "template_type": x["template_type"], "plays": x["plays"], "questions": x["questions_per_unit"]} for x in p["parts"]], "sources": p["sources"]})  # fmt: skip
    return {"modes": profs, "skills": [{"id": s, "label": SKILL_LABELS_JA[s]} for s in SKILLS], "error_reasons": [
        {"id": "sound", "label": "音が取れない"}, {"id": "vocabulary", "label": "語彙"}, {"id": "numbers_negation", "label": "数字・否定"}, {"id": "inference", "label": "推論"},
        {"id": "organizing", "label": "情報整理"}, {"id": "focus", "label": "集中"}, {"id": "other", "label": "その他"}]}  # fmt: skip


# ---------------------------------------------------------------- home / plans / library


@app.get("/api/home", response_model=S.HomeOut)
def home(session: Session = Depends(db), user: m.User = Depends(current_user)):
    out = stats.home(session, user, now_utc())
    session.commit()
    return out


@app.post("/api/plans/today", response_model=S.PlanOut)
def plan_start(body: S.PlanStartIn, session: Session = Depends(db), user: m.User = Depends(current_user)):
    plan = plans.start_plan(session, user, now_utc(), body.kind, body.mode)
    session.commit()
    return plans.plan_dto(session, plan)


def _own_plan(session: Session, user: m.User, plan_id: str) -> m.DailyPlan:
    plan = session.get(m.DailyPlan, plan_id)
    if plan is None or plan.user_id != user.id:
        raise L.NotFound("plan not found")
    return plan


@app.get("/api/plans/{plan_id}", response_model=S.PlanOut)
def plan_get(plan_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)):
    return plans.plan_dto(session, _own_plan(session, user, plan_id))


@app.post("/api/plans/{plan_id}/rebuild", response_model=S.PlanOut)
def plan_rebuild(plan_id: str, body: S.RebuildIn, session: Session = Depends(db), user: m.User = Depends(current_user)):
    plan = plans.rebuild_remaining(session, user, plan_id, body.mode, now_utc())
    session.commit()
    return plans.plan_dto(session, plan)


@app.post("/api/plans/{plan_id}/reserve")
def plan_reserve(plan_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    out = plans.reserve_slots(session, user, plan_id)
    session.commit()
    return {"slots": out}


@app.post("/api/plans/{plan_id}/release")
def plan_release(plan_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    n = plans.release_slots(session, user, plan_id)
    session.commit()
    return {"released": n}


@app.post("/api/slots/{slot_id}/replace")
def slot_replace(slot_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    out = plans.replace_slot(session, user, slot_id, now_utc())
    session.commit()
    return out


@app.get("/api/library", response_model=S.LibraryOut)
def library(mode: str | None = None, session: Session = Depends(db), user: m.User = Depends(current_user)):
    return stats.library(session, user, now_utc(), mode)


# ---------------------------------------------------------------- attempts


@app.post("/api/attempts", response_model=S.AttemptOut)
def attempt_start(body: S.AttemptStartIn, session: Session = Depends(db), user: m.User = Depends(current_user)):
    att = L.start_attempt(
        session, user, attempt_id=body.attempt_id, unit_id=body.unit_id, slot_id=body.slot_id, now=now_utc()
    )
    session.commit()
    return L.attempt_dto(session, att)


@app.get("/api/attempts/{attempt_id}", response_model=S.AttemptOut)
def attempt_get(attempt_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)):
    return L.attempt_dto(session, L._open_attempt(session, user, attempt_id, lock=False))


@app.post("/api/attempts/{attempt_id}/plays", response_model=S.PlayOut)
def attempt_play(attempt_id: str, body: S.PlayIn, session: Session = Depends(db), user: m.User = Depends(current_user)):
    out = L.record_play(session, user, attempt_id, event_id=body.event_id, segment_id=body.segment_id, rate=body.rate)
    session.commit()
    return out


@app.post("/api/attempts/{attempt_id}/aids")
def attempt_aid(
    attempt_id: str, body: S.AidIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    """Dedicated path for explicit help. The assisted flag is committed before any text is returned."""
    out = L.request_aid(session, user, attempt_id, event_id=body.event_id, kind=body.kind)
    session.commit()
    return out


@app.post("/api/attempts/{attempt_id}/submit", response_model=S.ResultOut)
def attempt_submit(
    attempt_id: str, body: S.SubmitIn, session: Session = Depends(db), user: m.User = Depends(current_user)
):
    att = L.submit_attempt(session, user, attempt_id, body.answers, now=now_utc())
    session.commit()
    return L.result_dto(session, att)


@app.get("/api/attempts/{attempt_id}/result", response_model=S.ResultOut)
def attempt_result(attempt_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)):
    out = L.result_dto(session, L._open_attempt(session, user, attempt_id, lock=False))
    out["missed"] = feedback.missed_ids(session, user, out["unit_id"])
    return out


@app.post("/api/units/{unit_id}/reports")
def unit_report(
    unit_id: str, body: S.ReportIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = feedback.report(session, user, unit_id, body.kind, body.note, body.attempt_id, now_utc())
    session.commit()
    return out


@app.post("/api/units/{unit_id}/missed")
def unit_missed(
    unit_id: str, body: S.MissedIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = feedback.set_missed(session, user, unit_id, body.utterance_id, body.missed)
    session.commit()
    return {"missed": out}


@app.get("/api/missed")
def missed_lines(
    mode: str | None = None, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    return {"lines": feedback.missed_list(session, user, mode)}


@app.post("/api/attempts/{attempt_id}/ack", response_model=S.AckOut)
def attempt_ack(attempt_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)):
    out = L.ack_feedback(session, user, attempt_id, now=now_utc())
    session.commit()
    return out


@app.post("/api/attempts/{attempt_id}/error-reasons")
def attempt_reason(
    attempt_id: str, body: S.ErrorReasonIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = L.set_error_reason(session, user, attempt_id, body.question_id, body.reason)
    session.commit()
    return {"self_reasons": out}


@app.post("/api/sync/answers")
def sync_answers(
    body: S.SyncIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = plans.sync_offline_answers(session, user, [i.model_dump() for i in body.items], now_utc())
    session.commit()
    return {"results": out}


# ---------------------------------------------------------------- radio


@app.post("/api/radio/programs")
def radio_create(
    body: S.RadioProgramIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    """The learner explicitly asks for listen-through audio. Answer access is recorded for every unit in it."""
    if body.mode not in MODES:
        raise L.Invalid("unknown mode")
    prog = radio.build_program(
        session,
        user,
        body.mode,
        now_utc(),
        target_sec=body.minutes * 60 if body.minutes else None,
        unit_ids=body.unit_ids,
        include_new=body.include_new,
    )
    session.commit()
    return radio.program_dto(session, prog)


@app.get("/api/radio/programs")
def radio_list(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    rows = (
        session.execute(
            select(m.RadioProgram)
            .where(m.RadioProgram.user_id == user.id)
            .order_by(m.RadioProgram.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    return {"programs": [radio.program_dto(session, p) for p in rows]}


@app.get("/api/radio/programs/{program_id}")
def radio_get(program_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    return radio.program_dto(session, radio.get_program(session, user, program_id))


@app.post("/api/radio/programs/{program_id}/progress")
def radio_progress(
    program_id: str, body: S.RadioProgressIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = radio.report_progress(
        session,
        user,
        program_id,
        event_id=body.event_id,
        ranges=[list(r) for r in body.ranges],
        position_ms=body.position_ms,
        now=now_utc(),
    )
    session.commit()
    return out


@app.post("/api/radio/self-reports")
def radio_self_report(
    body: S.SelfReportIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = radio.self_report(
        session,
        user,
        event_id=body.event_id,
        unit_id=body.unit_id,
        value=body.value,
        session_id=body.session_id,
        now=now_utc(),
    )
    session.commit()
    return out


@app.post("/api/radio/relisten")
def radio_relisten(
    body: S.UnitIdIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = radio.schedule_relisten(session, user, body.unit_id, now_utc())
    session.commit()
    return out


# ---------------------------------------------------------------- assessment


@app.get("/api/assessments/sets")
def assessment_sets(mode: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    if mode not in MODES:
        raise L.Invalid("unknown mode")
    rows = (
        session.execute(
            select(m.MockSet)
            .where(m.MockSet.realm == user.realm, m.MockSet.mode == mode, m.MockSet.status == "READY")
            .order_by(m.MockSet.created_at)
        )
        .scalars()
        .all()
    )
    open_sessions = (
        session.execute(
            select(m.LearningSession)
            .where(
                m.LearningSession.user_id == user.id,
                m.LearningSession.session_type == "assessment",
                m.LearningSession.mode == mode,
            )
            .order_by(m.LearningSession.started_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    p = current_profile(mode)
    return {"sets": [assessment.mock_set_dto(x) for x in rows], "exam": p["exam"], "profile_label": p["label_ja"],
            "sessions": [{"session_id": s.id, "status": s.status, "started_at": s.started_at.isoformat(), "result": s.result} for s in open_sessions]}  # fmt: skip


@app.post("/api/assessments")
def assessment_start(
    body: S.AssessmentStartIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    try:
        sess = assessment.start(session, user, body.mock_set_id, now_utc())
    except assessment.StaleSet:
        session.commit()  # keep the STALE mark
        raise
    session.commit()
    return assessment.paper_dto(session, sess)


@app.get("/api/assessments/{session_id}")
def assessment_get(
    session_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    return assessment.paper_dto(session, assessment.get_session(session, user, session_id))


@app.post("/api/assessments/{session_id}/submit")
def assessment_submit(
    session_id: str, body: S.AssessmentSubmitIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = assessment.submit(session, user, session_id, body.answers, now_utc())
    session.commit()
    return out


@app.get("/api/assessments/{session_id}/result")
def assessment_result(
    session_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    return assessment.result_dto(session, user, session_id)


# ---------------------------------------------------------------- stats / history / export


@app.get("/api/stats/growth")
def growth(
    mode: str, days: int = 30, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    if mode not in MODES or not (1 <= days <= 365):
        raise L.Invalid("bad mode or period")
    out = stats.growth(session, user, now_utc(), mode, days)
    session.commit()
    return out


@app.get("/api/history")
def history(
    mode: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(db),
    user: m.User = Depends(current_user),
) -> dict[str, Any]:
    return stats.history(session, user, min(max(limit, 1), 200), max(offset, 0), mode)


@app.get("/api/schedules")
def schedules(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    return {"schedules": stats.schedules(session, user, now_utc())}


@app.get("/api/export")
def export(session: Session = Depends(db), user: m.User = Depends(current_user)) -> Response:
    body = backup.export_user(session, user)
    return Response(
        json.dumps(body, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="listening-quest-export-{now_utc():%Y%m%d}.json"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/restore")
def restore(body: S.RestoreIn, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    out = backup.restore_user(session, user, body.export, apply_settings=body.apply_settings)
    if out["ok"]:
        session.commit()
    else:
        session.rollback()
    return out


# ---------------------------------------------------------------- audio (authenticated, Range)


def _may_fetch(session: Session, user: m.User, asset: m.Asset) -> bool:
    if asset.kind == "program":
        return asset.meta.get("owner") == user.id
    unit = session.get(m.Unit, asset.unit_id) if asset.unit_id else None
    if unit is None or unit.realm != user.realm:
        return False
    A = m.Attempt
    if asset.kind == "stimulus":
        if unit.pool == "assessment":
            return False
        if session.execute(select(A.id).where(A.user_id == user.id, A.unit_id == unit.id).limit(1)).first():
            return True
        return (
            session.execute(
                select(m.PlanSlot.id)
                .join(m.DailyPlan, m.DailyPlan.id == m.PlanSlot.plan_id)
                .where(m.DailyPlan.user_id == user.id, m.PlanSlot.unit_id == unit.id, m.PlanSlot.reserved.is_(True))
                .limit(1)
            ).first()
            is not None
        )
    if asset.kind == "exam":
        rows = session.execute(
            select(m.LearningSession).where(
                m.LearningSession.user_id == user.id, m.LearningSession.session_type == "assessment"
            )
        ).scalars()
        return any(unit.id in [i["unit_id"] for i in s.meta.get("items", [])] for s in rows)
    if asset.kind in ("feedback", "utterance"):  # only after the unit's answers were submitted
        return (
            session.execute(
                select(A.id).where(A.user_id == user.id, A.unit_id == unit.id, A.status == "submitted").limit(1)
            ).first()
            is not None
        )
    return False  # unit radio tracks are only reachable inside a program the learner asked for


@app.head("/api/audio/{asset_id}", include_in_schema=False)
@app.get("/api/audio/{asset_id}")
def audio(
    asset_id: str, request: Request, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> Response:
    asset = session.get(m.Asset, asset_id) if _ID.match(asset_id) else None
    if asset is None or not _may_fetch(session, user, asset):
        raise HTTPException(404, "audio not available")
    st = get_storage()
    size = asset.bytes
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "ETag": f'"{asset.sha256[:32]}"',
        "X-Content-Type-Options": "nosniff",
    }
    rng = request.headers.get("range")
    if rng:
        mt = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if not mt or (not mt.group(1) and not mt.group(2)):
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        if mt.group(1):
            start = int(mt.group(1))
            end = min(int(mt.group(2)), size - 1) if mt.group(2) else size - 1
        else:
            start, end = max(0, size - int(mt.group(2))), size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
        body = b"" if request.method == "HEAD" else st.get_range(asset.storage_key, start, end)
        return Response(body, status_code=206, media_type=asset.mime, headers=headers)
    headers["Content-Length"] = str(size)
    return Response(
        b"" if request.method == "HEAD" else st.get(asset.storage_key), media_type=asset.mime, headers=headers
    )


# ---------------------------------------------------------------- material management


@app.get("/api/admin/overview")
def admin_overview(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    out = admin.system_overview(session, user.realm)
    inv = replenish.inventory(session, user, L.user_settings(user)["active_mode"], now_utc())
    inv.pop("state")
    session.commit()
    return {**out, "inventory": inv}


@app.get("/api/admin/diagnostics")
def admin_diagnostics(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    return diagnostics.run(session, user.realm)


@app.get("/api/admin/costs")
def admin_costs(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    return admin.cost_report(session, user.realm)


@app.patch("/api/admin/system")
def admin_system(
    body: S.SystemPatch, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "replenish_modes" in patch and not set(patch["replenish_modes"]) <= set(MODES):
        raise L.Invalid("unknown mode in replenish_modes")
    try:
        out = budget.set_system(session, user.realm, patch)
    except ValueError as e:
        raise L.Invalid(str(e)) from e
    session.add(
        m.AuditLog(
            realm=user.realm, actor=user.username, action="system_settings_changed", target="system", details=patch
        )
    )
    queue.release_blocked(
        session, user.realm, ("paid_disabled", "budget", "monthly_budget", "job_cap", "no_provider", "missing_audio")
    )  # re-check with the new settings
    session.commit()
    return out


@app.get("/api/admin/verify-live")
def admin_verify_status(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    from lq.services import verification as V

    cfg = budget.get_system(session, user.realm)
    jid = V.latest_job_id(session, user.realm)
    return {
        "realm": user.realm,
        "running": bool(cfg.get("verification")),
        "cap_usd": (cfg.get("verification") or {}).get("cap_usd"),
        "problems": V.preflight(session, user.realm, max(V.budget_status(session, user.realm)["remaining_usd"], 1e-6))
        if user.realm == "live"
        else ["デモ領域では実APIを使いません"],
        "budget": V.budget_status(session, user.realm),
        "expected": V.expected_cost(),
        "tts_provider": get_settings().tts_provider,
        "report": V.report(session, jid) if jid and session.get(m.GenerationJob, jid) else None,
        "spec": {"mode": "common_test", "part": "3", "label_ja": "共通テスト 第3問（短い対話・1問）"},
    }


@app.post("/api/admin/verify-live")
def admin_verify_start(
    body: S.VerifyLiveIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    """Start the one-unit real-API check. Only this job may spend, up to cap_usd; the worker runs it and the
    allowance is withdrawn as soon as it ends."""
    from lq.services import verification as V

    if user.realm != "live":
        raise L.Invalid("live verification runs only in the live data area")
    remaining = V.budget_status(session, user.realm)["remaining_usd"]
    cap = remaining if body.cap_usd is None else min(body.cap_usd, remaining)
    problems = V.preflight(session, user.realm, cap, probe=True) if cap > 0 else ["the verification budget is used up"]
    if problems:
        raise L.Invalid(" / ".join(problems))
    p = current_profile("common_test")
    part = next(x for x in p["parts"] if x["part"] == "3")
    spec = {"mode": "common_test", "part": "3", "template_type": part["template_type"], "primary_skill": "numbers_negation", "band": 3, "topic_hint": "",
            "seed": f"verify-{new_id()[:10]}", "source": "generated", "pool": "practice"}  # fmt: skip
    jid = V.start(session, user.realm, cap, spec, actor=user.username)
    session.commit()
    return {"job_id": jid}


@app.get("/api/admin/jobs")
def admin_jobs(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    return {"jobs": admin.list_jobs(session, user.realm)}


@app.post("/api/admin/jobs")
def admin_generate(
    body: S.GenerateIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    if body.mode not in MODES or body.primary_skill not in SKILLS:
        raise L.Invalid("unknown mode or skill")
    part = next((p for p in current_profile(body.mode)["parts"] if p["part"] == body.part), None)
    if part is None:
        raise L.Invalid("unknown part for this mode")
    ids = []
    batch = new_id()[:10]
    for i in range(body.count):
        params = {"mode": body.mode, "part": body.part, "template_type": part["template_type"], "primary_skill": body.primary_skill, "band": body.band, "topic_hint": body.topic_hint,
                  "seed": f"manual-{batch}-{i}", "source": "generated", "pool": body.pool}  # fmt: skip
        job, _ = queue.enqueue(session, user.realm, "generate", params)
        ids.append(job.id)
    session.commit()
    return {"job_ids": ids}


@app.post("/api/admin/jobs/{job_id}/retry")
def admin_retry(job_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    job = session.get(m.GenerationJob, job_id)
    if job is None or job.realm != user.realm:
        raise L.NotFound("job not found")
    job = queue.manual_retry(session, job_id)
    unit = session.get(m.Unit, job.unit_id) if job.unit_id else None
    if unit is not None and unit.status in ("FAILED", "AUDIO_GENERATING"):
        unit.status = "AUDIO_PENDING"
    session.commit()
    return admin.job_dto(session, job)


@app.post("/api/admin/jobs/{job_id}/rewrite")
def admin_rewrite(job_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    try:
        job, created = admin.rewrite_quarantined(session, user.realm, job_id)
    except LookupError as e:
        raise L.NotFound(str(e)) from e
    except ValueError as e:
        raise L.Invalid(str(e)) from e
    session.commit()
    return {"job_id": job.id, "created": created}


@app.get("/api/admin/storage")
def admin_storage(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    return admin.storage_summary(session, user.realm)


@app.post("/api/admin/storage/cleanup")
def admin_storage_cleanup(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    out = admin.cleanup_unusable(session, user.realm)
    session.commit()
    return out


@app.get("/api/admin/units")
def admin_units(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    return {"units": admin.list_units(session, user.realm)}


@app.post("/api/admin/units/{unit_id}/approve")
def admin_approve(
    unit_id: str, body: S.ApproveIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    unit = L.get_unit_for(session, user, unit_id)
    rec = importer.manual_approve(session, unit, target=body.target, reviewer=user.username, note=body.note)
    session.commit()
    return {"record": {"method": rec.method, "target": rec.target, "content_hash": rec.content_hash[:12]}}


@app.get("/api/admin/reports")
def admin_reports(
    all: bool = False, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    return {"reports": feedback.list_reports(session, user.realm, include_resolved=all)}


@app.post("/api/admin/reports/{report_id}/resolve")
def admin_resolve_report(
    report_id: str, body: S.ResolveReportIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = feedback.resolve(session, user, report_id, body.action, now_utc())
    session.commit()
    return out


@app.post("/api/admin/units/{unit_id}/restore")
def admin_restore(unit_id: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    out = feedback.restore_unit(session, user, unit_id)
    session.commit()
    return out


@app.post("/api/admin/units/{unit_id}/invalidate")
def admin_invalidate(
    unit_id: str, body: S.InvalidateIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    out = admin.invalidate_unit(session, user, unit_id, body.reason, now_utc())
    session.commit()
    return out


async def _read_import(manifest: UploadFile, files: list[UploadFile]) -> tuple[dict, dict[str, bytes]]:
    raw = await manifest.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise L.Invalid("manifest larger than 5 MB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise L.Invalid(f"manifest is not valid JSON: {e}") from e
    blobs: dict[str, bytes] = {}
    for f in files:
        name = (f.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        blob = await f.read(importer.MAX_FILE_BYTES + 1)
        blobs[name] = blob
    return data, blobs


@app.post("/api/admin/import")
async def admin_import(
    manifest: UploadFile = File(...),
    files: list[UploadFile] = File(default=[]),
    session: Session = Depends(db),
    user: m.User = Depends(current_user),
) -> dict[str, Any]:
    data, blobs = await _read_import(manifest, files)
    out = importer.import_manifest(session, user.realm, data, blobs, actor=f"ui:{user.username}")
    session.commit()
    return out


@app.post("/api/import")
async def cli_import(
    manifest: UploadFile = File(...),
    files: list[UploadFile] = File(default=[]),
    realm: str = Form(default="live"),
    authorization: str | None = Header(default=None),
    session: Session = Depends(db),
) -> dict[str, Any]:
    """Machine import for the generator CLI. Bearer token (LQ_IMPORT_TOKEN); no cookies involved."""
    token = get_settings().import_token
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not token or not given or not hmac.compare_digest(given, token):
        raise HTTPException(401, "import token missing or wrong")
    if realm not in ("live", "demo"):
        raise L.Invalid("realm must be live or demo")
    data, blobs = await _read_import(manifest, files)
    out = importer.import_manifest(session, realm, data, blobs, actor="cli")
    session.commit()
    return out


@app.get("/api/admin/mock")
def admin_mock(mode: str, session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    if mode not in MODES:
        raise L.Invalid("unknown mode")
    sets = (
        session.execute(
            select(m.MockSet)
            .where(m.MockSet.realm == user.realm, m.MockSet.mode == mode)
            .order_by(m.MockSet.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    return {
        "inventory": assessment.reserved_inventory(session, user.realm, mode),
        "sets": [assessment.mock_set_dto(x) for x in sets],
    }


@app.post("/api/admin/mock/generate")
def admin_mock_generate(
    body: S.ModeIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    if body.mode not in MODES:
        raise L.Invalid("unknown mode")
    n = assessment.enqueue_missing(session, user.realm, body.mode, new_id()[:8])
    session.commit()
    return {"queued": n}


@app.post("/api/admin/mock/assemble")
def admin_mock_assemble(
    body: S.ModeIn, session: Session = Depends(db), user: m.User = Depends(current_user)
) -> dict[str, Any]:
    if body.mode not in MODES:
        raise L.Invalid("unknown mode")
    try:
        ms = assessment.assemble(session, user.realm, body.mode)
    except assessment.Shortage as e:
        return {
            "assembled": False,
            "shortage": e.detail,
            "message": "素材がそろっていません。不足分を生成してから組み立ててください。",
        }
    session.commit()
    return {"assembled": True, "set": assessment.mock_set_dto(ms)}


@app.post("/api/admin/replenish/run")
def admin_replenish(session: Session = Depends(db), user: m.User = Depends(current_user)) -> dict[str, Any]:
    n = replenish.run_for_user(session, user, now_utc())
    session.commit()
    cfg = budget.get_system(session, user.realm)
    why = None
    if n == 0:
        why = (
            "自動補充がOFFです"
            if not cfg["auto_replenish"]
            else (
                "有料生成がOFFです"
                if user.realm != "demo" and not cfg["paid_enabled"]
                else "在庫は十分、または本日の生成上限に達しています"
            )
        )
    return {"queued": n, "reason": why}


@app.get("/api/schema/unit")
def schema_unit(user: m.User = Depends(current_user)) -> dict[str, Any]:
    return unit_json_schema()


@app.get("/api/admin/profiles")
def admin_profiles(user: m.User = Depends(current_user)) -> dict[str, Any]:
    return {"profiles": list(load_profiles().values())}

"""One-unit live verification with a hard cost cap, shared by `lq verify-live` and the admin screen.

While a verification is registered, exactly that job may spend money (up to cap_usd, every step and retry
included) even though paid generation stays off for everything else. It is cleared as soon as the job ends."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from lq.config import get_settings
from lq.db import models as m
from lq.generator import budget
from lq.jobs import queue

TERMINAL = ("SUCCEEDED", "FAILED", "BLOCKED")


def budget_status(session, realm: str) -> dict:
    """The verification budget does not reset when moving environments: total minus what was spent in other
    environments (LQ_VERIFY_BUDGET_PRIOR_USD) minus what every verification job here has been charged."""
    s = get_settings()
    jobs = [
        j.id
        for j in session.execute(select(m.GenerationJob).where(m.GenerationJob.realm == realm)).scalars()
        if j.params.get("verification")
    ]
    rows = session.execute(select(m.CostEntry).where(m.CostEntry.job_id.in_(jobs))).scalars().all() if jobs else []
    here = budget.cost_summary(rows)
    remaining = round(s.verify_budget_total_usd - s.verify_budget_prior_usd - here["counted_usd"], 6)
    return {
        "total_usd": s.verify_budget_total_usd,
        "prior_environments_usd": s.verify_budget_prior_usd,
        "this_environment": here,
        "remaining_usd": max(0.0, remaining),
        "runs_here": len(jobs),
    }


def expected_cost(mode: str = "common_test", part: str = "3") -> dict:
    return budget.estimate_unit(mode, part, get_settings())


def preflight(session, realm: str, cap_usd: float, *, rehearsal: bool = False, probe: bool = False) -> list[str]:
    s = get_settings()
    problems = []
    if not (0 < cap_usd <= 20):
        problems.append("the cost cap must be more than 0 and at most 20 USD")
    if not rehearsal:
        bs = budget_status(session, realm)
        est = expected_cost()
        if est["total_usd"] is None:
            problems.append("the expected cost cannot be computed (unknown price)")
        elif est["total_usd"] > min(cap_usd, bs["remaining_usd"]):
            problems.append(
                f"not enough verification budget left to finish one unit: remaining {bs['remaining_usd']:.4f} USD (cap {cap_usd:.4f}), expected up to {est['total_usd']:.4f} USD; nothing was started"
            )
    if not rehearsal:
        if not s.openai_api_key:
            problems.append("OPENAI_API_KEY is not set on the server")
        table = budget.price_table()
        checks = (
            ("content", s.content_model in table["text"]),
            ("review", s.review_model in table["text"]),
            (
                "tts",
                (s.tts_model in table["realtime"])
                if s.tts_provider != "openai_speech"
                else (s.tts_speech_model in table["speech"]),
            ),
            ("asr", s.asr_model in table["transcription"]),
        )
        problems += [f"no price for the {label} model: refusing to treat it as free" for label, ok in checks if not ok]
        if s.openai_api_key and not problems and probe:
            from lq.generator.openai_provider import probe_auth

            problems += probe_auth(
                s.openai_api_key,
                s.openai_base_url,
                None if s.tts_provider == "openai_speech" else s.openai_realtime_url,
                s.tts_model,
            )
    cfg = budget.get_system(session, realm)
    if cfg["paid_enabled"]:
        problems.append("paid generation is enabled for everything; turn it off so only the verification job can spend")
    if cfg.get("verification"):
        problems.append("another verification is still running")
    return problems


def start(session, realm: str, cap_usd: float, spec: dict, *, actor: str) -> str:
    job, _ = queue.enqueue(session, realm, "generate", {**spec, "verification": True})
    budget.set_system(
        session,
        realm,
        {
            "verification": {"job_id": job.id, "cap_usd": cap_usd, "started_at": datetime.now(UTC).isoformat()},
            "last_verification_job_id": job.id,
        },
    )
    session.add(
        m.AuditLog(
            realm=realm,
            actor=actor,
            action="live_verification_started",
            target=job.id,
            details={"cap_usd": cap_usd, "spec": spec},
        )
    )
    session.flush()
    return job.id


def finish(session, realm: str, job_id: str, *, actor: str) -> None:
    """Close the verification: no further spending for it, and a job left waiting is ended."""
    cfg = budget.get_system(session, realm, lock=True)
    if (cfg.get("verification") or {}).get("job_id") == job_id:
        budget.set_system(session, realm, {"verification": None})
    job = session.get(m.GenerationJob, job_id)
    if job is not None and job.status in ("QUEUED", "RETRY_WAIT", "BLOCKED"):
        job.status, job.stop_detail = "FAILED", (job.stop_detail or "") + " | verification ended"
    session.add(
        m.AuditLog(
            realm=realm,
            actor=actor,
            action="live_verification_finished",
            target=job_id,
            details={"job_status": job.status if job else None, "cost": budget.job_cost(session, job_id)},
        )
    )


def finish_if_done(session, job_id: str) -> None:
    """Called by the worker after each job: closes a verification whose job has ended."""
    job = session.get(m.GenerationJob, job_id)
    if job is None or not job.params.get("verification") or job.status not in TERMINAL:
        return
    cfg = budget.get_system(session, job.realm)
    if (cfg.get("verification") or {}).get("job_id") == job_id:
        finish(session, job.realm, job_id, actor="worker")


def latest_job_id(session, realm: str) -> str | None:
    return budget.get_system(session, realm).get("last_verification_job_id")


def report(db, job_id: str) -> dict:
    from lq.services import admin

    job = db.get(m.GenerationJob, job_id)
    unit = db.get(m.Unit, job.unit_id) if job.unit_id else None
    entries = (
        db.execute(select(m.CostEntry).where(m.CostEntry.job_id == job_id).order_by(m.CostEntry.created_at))
        .scalars()
        .all()
    )
    reviews = (
        db.execute(select(m.ReviewRecord).where(m.ReviewRecord.unit_id == unit.id)).scalars().all() if unit else []
    )
    assets = db.execute(select(m.Asset).where(m.Asset.unit_id == unit.id)).scalars().all() if unit else []
    audio_review = next((r for r in reviews if r.target == "audio"), None)
    return {
        "job_id": job_id, "job_status": job.status, "stop_detail": job.stop_detail, "last_completed_step": job.last_completed_step,
        "unit_id": unit.id if unit else None, "unit_status": unit.status if unit else None, "unit_status_reason": unit.status_reason if unit else None,
        "title": unit.title if unit else None, "audio_kind": unit.audio_kind if unit else None, "audio_review_state": unit.audio_review_state if unit else None,
        "content_review": [{"method": r.method, "reviewer": r.reviewer, "result": r.result, "problems": r.details.get("problems")} for r in reviews if r.target == "content"],
        "audio_check": {"method": audio_review.method, "result": audio_review.result, "clips": audio_review.details.get("clips"), "mismatches": audio_review.details.get("mismatches")} if audio_review else None,
        "assets": [{"role": a.role, "duration_ms": a.duration_ms, "bytes": a.bytes} for a in assets if a.kind != "utterance"],
        "cost": budget.job_cost(db, job_id),
        "budget_after": budget_status(db, job.realm),
        "cap_usd": next((a.details.get("cap_usd") for a in db.execute(select(m.AuditLog).where(m.AuditLog.target == job_id, m.AuditLog.action == "live_verification_started")).scalars()), None),
        "cost_entries": [{"step": e.step, "model": e.model, "state": e.state, "pre_estimate_usd": round(e.estimated_usd, 6), "charged_usd": e.actual_usd, "usage": e.usage} for e in entries],
        "price_source": budget.price_table()["source"], "price_checked_at": budget.price_table()["checked_at"],
        "open_in_app": f"/play?unit={unit.id}" if unit and unit.status == "READY" else None,
        "job": admin.job_dto(db, job),
    }  # fmt: skip

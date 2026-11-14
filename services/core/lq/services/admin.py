"""Material management: listing, invalidation with recomputation, job views."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from lq.db import models as m
from lq.generator import budget
from lq.services import learning as L
from lq.services import plans, stats

BLOCK_LABELS = {
    "paid_disabled": "有料生成がOFFのため待機中", "budget": "日次予算の上限で待機中", "monthly_budget": "月の予算の上限で待機中（翌月に再開）", "job_cap": "1ジョブ上限で待機中", "price_unknown": "単価不明のため実行不可",
    "no_provider": "生成プロバイダー未設定", "awaiting_content_review": "内容の手動承認待ち", "awaiting_audio_approval": "音声の手動確認待ち", "missing_audio": "音声が不足（音声合成が未設定）", "verification_cap": "検証の費用上限に到達したため停止",
}  # fmt: skip


def job_dto(session, job: m.GenerationJob) -> dict:
    unit = session.get(m.Unit, job.unit_id) if job.unit_id else None
    return {
        "id": job.id, "kind": job.kind, "status": job.status, "blocked_reason": job.blocked_reason, "blocked_label": BLOCK_LABELS.get(job.blocked_reason or "", None),
        "stop_detail": job.stop_detail, "last_completed_step": job.last_completed_step, "attempts": job.attempts, "max_attempts": job.max_attempts,
        "params": {k: job.params.get(k) for k in ("mode", "part", "template_type", "primary_skill", "band", "pool", "origin")}, "unit_id": job.unit_id,
        "unit_status": unit.status if unit else None, "unit_title": unit.title if unit else None, "unit_status_reason": unit.status_reason if unit else None,
        "cost": budget.job_cost(session, job.id), "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(),
        "run_after": job.run_after.isoformat() if job.status in ("RETRY_WAIT", "BLOCKED") else None,
    }  # fmt: skip


def list_jobs(session, realm: str, limit: int = 60) -> list[dict]:
    rows = (
        session.execute(
            select(m.GenerationJob)
            .where(m.GenerationJob.realm == realm)
            .order_by(m.GenerationJob.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [job_dto(session, j) for j in rows]


def list_units(session, realm: str, limit: int = 300) -> list[dict]:
    rows = (
        session.execute(select(m.Unit).where(m.Unit.realm == realm).order_by(m.Unit.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    out = []
    for u in rows:
        recs = (
            session.execute(
                select(m.ReviewRecord)
                .where(m.ReviewRecord.unit_id == u.id, m.ReviewRecord.content_hash == u.content_hash)
                .order_by(m.ReviewRecord.created_at)
            )
            .scalars()
            .all()
        )
        out.append({
            **plans.unit_card(u), "source": u.source, "content_id": u.content_id, "revision": u.revision, "status": u.status, "status_reason": u.status_reason, "pool": u.pool,
            "session_types": u.session_types, "audio_review_state": u.audio_review_state, "content_hash": u.content_hash[:12], "invalidated": u.invalidated_at is not None,
            "invalidated_reason": u.invalidated_reason, "superseded": u.superseded_by is not None,
            "reviews": [{"target": r.target, "method": r.method, "reviewer": r.reviewer, "result": r.result, "at": r.created_at.isoformat()} for r in recs],
        })  # fmt: skip
    return out


def invalidate_unit(session, actor: m.User, unit_id: str, reason: str, now: datetime | None = None) -> dict:
    """Withdraw a wrongly generated unit. History stays; statistics and recommendations exclude it and
    derived state (difficulty bands) is recomputed. Pending plan slots get another unit."""
    now = now or datetime.now(UTC)
    unit = L.get_unit_for(session, actor, unit_id)
    if unit.invalidated_at is None:
        unit.invalidated_at = now
        unit.invalidated_reason = reason[:500]
        session.add(
            m.AuditLog(
                realm=unit.realm,
                actor=actor.username,
                action="unit_invalidated",
                target=f"{unit.source}/{unit.content_id}@{unit.revision}",
                details={"unit_id": unit.id, "title": unit.title, "reason": reason[:500]},
            )
        )
    session.flush()
    affected = session.execute(select(m.User).where(m.User.realm == unit.realm)).scalars().all()
    recomputed, replaced = [], 0
    for u in affected:
        recomputed.append({"user": u.username, **stats.recompute_mode_state(session, u, unit.mode)})
        slots = (
            session.execute(
                select(m.PlanSlot)
                .join(m.DailyPlan, m.DailyPlan.id == m.PlanSlot.plan_id)
                .where(
                    m.DailyPlan.user_id == u.id,
                    m.DailyPlan.status == "active",
                    m.PlanSlot.unit_id == unit.id,
                    m.PlanSlot.state.in_(("pending", "started")),
                )
            )
            .scalars()
            .all()
        )
        for s in slots:
            s.reserved = False
            att = session.get(m.Attempt, s.attempt_id) if s.attempt_id else None
            if att is not None and att.status == "open":
                s.attempt_id = None
            if plans.replace_slot(session, u, s.id, now, reason="invalidated").get("replaced"):
                replaced += 1
    return {"unit_id": unit.id, "invalidated": True, "recomputed": recomputed, "slots_replaced": replaced}


def system_overview(session, realm: str) -> dict:
    cfg = budget.get_system(session, realm)
    from lq.config import get_settings

    s = get_settings()
    table = budget.price_table()
    return {
        "settings": cfg, "spend_today": budget.spent_today(session, realm), "spend_month": budget.spent_month(session, realm), "api_key_present": bool(s.openai_api_key) if realm != "demo" else False,
        "providers": {"content_model": s.content_model, "review_model": s.review_model, "tts_provider": s.tts_provider, "tts_model": s.tts_model if s.tts_provider != "openai_speech" else s.tts_speech_model, "asr_model": s.asr_model} if realm != "demo" else {"note": "demo providers（無料・音声動作確認用）"},
        "prices": {"source": table["source"], "checked_at": table["checked_at"], "note": table["note"]},
        "disclaimer": "上限はこのアプリが新規に開始するリクエストを止めるためのものです。実行中の処理等があるため、請求額の絶対的な保証ではありません。",
    }  # fmt: skip


def cost_report(session, realm: str, today=None, days: int = 31) -> dict:
    """What paid generation cost, per day and in total, and how much went into material that was not usable
    (quarantined / failed). Figures are this app's own accounting; the provider's billing page is authoritative."""
    from datetime import timedelta

    today = today or datetime.now(UTC).date()
    since = today - timedelta(days=days - 1)
    E, J, U = m.CostEntry, m.GenerationJob, m.Unit
    rows = (
        session.execute(select(E).where(E.realm == realm, E.budget_day >= since, E.budget_day <= today)).scalars().all()
    )
    daily: dict[str, float] = {}
    by_job: dict[str, float] = {}
    for e in rows:
        c = budget._counted(e)
        daily[e.budget_day.isoformat()] = daily.get(e.budget_day.isoformat(), 0.0) + c
        if e.job_id:
            by_job[e.job_id] = by_job.get(e.job_id, 0.0) + c
    jobs = (
        session.execute(select(J, U).outerjoin(U, U.id == J.unit_id).where(J.id.in_(list(by_job)))).all()
        if by_job
        else []
    )
    ready, wasted, wasted_n, other = [], 0.0, 0, 0.0
    for j, u in jobs:
        c = by_job[j.id]
        if u is not None and u.status == "READY":
            ready.append(c)
        elif j.status == "FAILED" or (u is not None and u.status in ("QUARANTINED", "FAILED")):
            wasted += c
            wasted_n += 1
        else:
            other += c  # still running, blocked or waiting for a retry
    week_since = today - timedelta(days=6)
    week = sum(v for d, v in daily.items() if d >= week_since.isoformat())
    series = []
    for i in range(days):
        d = (since + timedelta(days=i)).isoformat()
        series.append({"day": d, "counted_usd": round(daily.get(d, 0.0), 6)})
    return {
        "days": series,
        "last_7_days_usd": round(week, 6),
        "month": budget.spent_month(session, realm, today),
        "ready_units": len(ready),
        "avg_per_ready_unit_usd": round(sum(ready) / len(ready), 6) if ready else None,
        "wasted_usd": round(wasted, 6),
        "wasted_jobs": wasted_n,
        "in_progress_usd": round(other, 6),
        "note": "このアプリの計算（確定＋推計＋不明＋予約中）。実際の請求額は OpenAI の請求画面が正です。",
    }


def rewrite_quarantined(session, realm: str, job_id: str) -> tuple[m.GenerationJob, bool]:
    """Queue ONE new generation for a quarantined unit, with the quarantine reason passed to the writer as
    something to fix. Manual only (a button), paid like any generation and inside the same budgets; asking
    again for the same job returns the job already queued instead of paying twice."""
    from lq.jobs import queue

    job = session.get(m.GenerationJob, job_id)
    if job is None or job.realm != realm or job.kind != "generate":
        raise LookupError("job not found")
    unit = session.get(m.Unit, job.unit_id) if job.unit_id else None
    if unit is None or unit.status != "QUARANTINED":
        raise ValueError("only quarantined material can be rewritten")
    reason = (unit.status_reason or "").strip()[:1200]
    params = {k: v for k, v in (job.params or {}).items() if k != "extra"}
    params["seed"] = f"{params.get('seed', 'x')}-rw"
    params["extra"] = {"fix_notes": [f"An earlier draft for this request was rejected by quality checks: {reason}. "
                                     "Write a new unit that avoids this problem."]}  # fmt: skip
    params["origin"] = "rewrite"
    return queue.enqueue(session, realm, "generate", params, dedupe_key=f"rewrite:{job.id}")


UNUSABLE = ("QUARANTINED", "FAILED")


def storage_summary(session, realm: str) -> dict:
    """Bytes of stored audio by what it belongs to. Only audio of material no learner can reach is reclaimable."""
    A, U = m.Asset, m.Unit
    rows = session.execute(
        select(U.status, U.invalidated_at.is_(None), func.count(A.id), func.coalesce(func.sum(A.bytes), 0))
        .join(U, U.id == A.unit_id)
        .where(U.realm == realm)
        .group_by(U.status, U.invalidated_at.is_(None))
    ).all()
    out = {"usable_bytes": 0, "invalidated_bytes": 0, "unusable_bytes": 0, "unusable_files": 0, "other_bytes": 0}
    for status, active, n, b in rows:
        if status in UNUSABLE:
            out["unusable_bytes"] += int(b)
            out["unusable_files"] += int(n)
        elif not active:
            out["invalidated_bytes"] += int(b)  # kept: the learner's history still links to it
        elif status == "READY":
            out["usable_bytes"] += int(b)
        else:
            out["other_bytes"] += int(b)  # still being made
    return out


def cleanup_unusable(session, realm: str, older_than_days: int = 7, now: datetime | None = None) -> dict:
    """Delete the audio of quarantined / failed material older than `older_than_days`, plus orphaned files.
    The unit rows, reports and review records stay (only the sound files go), so nothing in the learner's
    history changes; such material was never offered to the learner."""
    from datetime import timedelta

    from lq.services.content import gc_orphan_objects
    from lq.storage import get_storage

    now = now or datetime.now(UTC)
    A, U = m.Asset, m.Unit
    rows = session.execute(
        select(A).join(U, U.id == A.unit_id).where(U.realm == realm, U.status.in_(UNUSABLE), U.created_at < now - timedelta(days=older_than_days))
    ).scalars().all()  # fmt: skip
    st = get_storage()
    freed = 0
    for a in rows:
        try:
            st.delete(a.storage_key)
        except FileNotFoundError:
            pass
        freed += a.bytes or 0
        session.delete(a)
    session.flush()
    orphans = gc_orphan_objects(session)
    return {"deleted_files": len(rows), "freed_bytes": int(freed), "orphans_removed": len(orphans)}

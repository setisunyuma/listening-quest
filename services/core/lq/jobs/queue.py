"""PostgreSQL job queue: leases with a generation counter, heartbeat, reclaim, bounded retry.

A worker may only write results while it still holds the current lease generation. A worker whose lease
expired and was reclaimed can therefore never finalise anything afterwards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update

from lq.config import get_settings
from lq.db import models as m
from lq.generator import budget


class StaleLease(Exception):
    pass


@dataclass(frozen=True)
class Lease:
    job_id: str
    worker_id: str
    generation: int

    def guard(self, session, now: datetime | None = None) -> m.GenerationJob:
        """Lock the job row and prove this worker still owns it. Call inside every writing transaction."""
        job = session.execute(
            select(m.GenerationJob).where(m.GenerationJob.id == self.job_id).with_for_update()
        ).scalar_one()
        if job.status != "RUNNING" or job.lease_owner != self.worker_id or job.lease_generation != self.generation:
            raise StaleLease(self.job_id)
        now = now or datetime.now(UTC)
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=get_settings().job_lease_sec)
        job.updated_at = now
        return job


def enqueue(
    session,
    realm: str,
    kind: str,
    params: dict,
    *,
    dedupe_key: str | None = None,
    unit_id: str | None = None,
    state: dict | None = None,
) -> tuple[m.GenerationJob, bool]:
    if dedupe_key:
        existing = session.execute(
            select(m.GenerationJob).where(m.GenerationJob.dedupe_key == dedupe_key)
        ).scalar_one_or_none()
        if existing:
            return existing, False
    job = m.GenerationJob(
        realm=realm,
        kind=kind,
        params=params,
        dedupe_key=dedupe_key,
        unit_id=unit_id,
        state=state or {},
        max_attempts=get_settings().job_max_attempts,
    )
    session.add(job)
    session.flush()
    return job, True


AUTO_RECHECK = ("budget", "job_cap", "concurrency")


def claim(session, worker_id: str, now: datetime | None = None) -> Lease | None:
    now = now or datetime.now(UTC)
    J = m.GenerationJob
    cond = or_(
        and_(J.status.in_(("QUEUED", "RETRY_WAIT")), J.run_after <= now),
        and_(J.status == "RUNNING", J.lease_expires_at < now),
        and_(J.status == "BLOCKED", J.blocked_reason.in_(AUTO_RECHECK), J.run_after <= now),
    )
    candidates = (
        session.execute(select(J).where(cond).order_by(J.created_at).limit(10).with_for_update(skip_locked=True))
        .scalars()
        .all()
    )
    for job in candidates:
        cfg = budget.get_system(session, job.realm, lock=True)
        if job.status == "RUNNING":  # previous worker vanished
            job.attempts += 1
            if job.attempts >= job.max_attempts:
                job.status, job.stop_detail = "FAILED", "worker lost repeatedly; retry limit reached"
                job.lease_owner = None
                job.updated_at = now
                continue
        live = budget.running_jobs(session, job.realm, now)
        if live >= int(cfg["max_concurrent_jobs"]):
            continue
        job.status = "RUNNING"
        job.blocked_reason = None
        job.lease_owner = worker_id
        job.lease_generation += 1
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=get_settings().job_lease_sec)
        job.updated_at = now
        session.flush()
        return Lease(job.id, worker_id, job.lease_generation)
    return None


def _finish(session, lease: Lease, **values) -> bool:
    values.setdefault("lease_owner", None)
    values["updated_at"] = datetime.now(UTC)
    J = m.GenerationJob
    res = session.execute(
        update(J)
        .where(
            J.id == lease.job_id,
            J.lease_owner == lease.worker_id,
            J.lease_generation == lease.generation,
            J.status == "RUNNING",
        )
        .values(**values)
    )
    return res.rowcount == 1


def succeed(session, lease: Lease, detail: str | None = None) -> bool:
    return _finish(session, lease, status="SUCCEEDED", stop_detail=detail, blocked_reason=None)


def fail(session, lease: Lease, detail: str) -> bool:
    return _finish(session, lease, status="FAILED", stop_detail=detail[:2000])


def block(session, lease: Lease, reason: str, detail: str, recheck_sec: int = 600) -> bool:
    """Waiting for budget / approval. Not a failure and not done."""
    return _finish(
        session,
        lease,
        status="BLOCKED",
        blocked_reason=reason,
        stop_detail=detail[:2000],
        run_after=datetime.now(UTC) + timedelta(seconds=recheck_sec),
    )


def retry_later(session, lease: Lease, detail: str) -> bool:
    s = get_settings()
    job = session.get(m.GenerationJob, lease.job_id)
    attempts = job.attempts + 1
    if attempts >= job.max_attempts:
        return _finish(
            session, lease, status="FAILED", attempts=attempts, stop_detail=f"retry limit reached: {detail}"[:2000]
        )
    wait = min(s.job_retry_base_sec * (2 ** (attempts - 1)), s.job_retry_max_sec)
    return _finish(
        session,
        lease,
        status="RETRY_WAIT",
        attempts=attempts,
        stop_detail=detail[:2000],
        run_after=datetime.now(UTC) + timedelta(seconds=wait),
    )


def manual_retry(session, job_id: str) -> m.GenerationJob:
    job = session.execute(select(m.GenerationJob).where(m.GenerationJob.id == job_id).with_for_update()).scalar_one()
    if job.status in ("FAILED", "BLOCKED", "RETRY_WAIT"):
        job.status, job.attempts, job.blocked_reason, job.run_after = "QUEUED", 0, None, datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
    return job


def release_blocked(session, realm: str, reasons: tuple[str, ...], unit_id: str | None = None) -> int:
    """Called when the blocking condition changed (settings updated, approval recorded)."""
    J = m.GenerationJob
    q = update(J).where(J.realm == realm, J.status == "BLOCKED", J.blocked_reason.in_(reasons))
    if unit_id:
        q = q.where(J.unit_id == unit_id)
    return session.execute(
        q.values(status="QUEUED", blocked_reason=None, run_after=datetime.now(UTC), updated_at=datetime.now(UTC))
    ).rowcount

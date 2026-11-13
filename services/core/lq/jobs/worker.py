"""Worker process: claims jobs, runs the pipeline, maps outcomes to job states, runs auto-replenish."""

from __future__ import annotations

import logging
import os
import socket
import time
import traceback

from lq.audio.pcm import AudioError
from lq.config import get_settings
from lq.db.base import session_factory
from lq.generator.budget import BudgetBlocked
from lq.generator.providers import ProviderError
from lq.jobs import pipeline, queue

log = logging.getLogger("lq.worker")


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_one(worker_id: str) -> str | None:
    """Claim and run at most one job. Returns the outcome, or None when nothing was claimable."""
    with session_factory()() as s:
        lease = queue.claim(s, worker_id)
        s.commit()
    if lease is None:
        return None
    outcome = "error"
    try:
        pipeline.execute(lease)
        with session_factory()() as s:
            outcome = "succeeded" if queue.succeed(s, lease) else "stale"
            s.commit()
    except queue.StaleLease:
        outcome = "stale"  # someone else owns the job now; write nothing
    except BudgetBlocked as b:
        with session_factory()() as s:
            # paid_disabled waits for a settings change; budget limits are re-checked periodically
            queue.block(s, lease, b.reason, b.detail, recheck_sec=900)
            s.commit()
        outcome = f"blocked:{b.reason}"
    except pipeline.Blocked as b:
        with session_factory()() as s:
            queue.block(s, lease, b.reason, b.detail, recheck_sec=3600)
            s.commit()
        outcome = f"blocked:{b.reason}"
    except pipeline.Terminal as t:
        with session_factory()() as s:
            queue.fail(s, lease, str(t))
            s.commit()
        outcome = "failed"
    except (ProviderError, AudioError) as e:
        with session_factory()() as s:
            if getattr(e, "retryable", True):
                queue.retry_later(s, lease, f"{type(e).__name__}: {e}")
                outcome = "retry"
            else:
                queue.fail(s, lease, f"{type(e).__name__}: {e}")
                outcome = "failed"
            s.commit()
    except Exception as e:  # unknown errors are retried a bounded number of times
        log.error("job %s crashed: %s", lease.job_id, traceback.format_exc())
        with session_factory()() as s:
            queue.retry_later(s, lease, f"{type(e).__name__}: {e}")
            s.commit()
        outcome = "retry"
    log.info("job %s -> %s", lease.job_id, outcome)
    return outcome


def drain(worker_id: str = "inline", max_jobs: int = 1000) -> list[str]:
    """Run until nothing is claimable (used by tests, the demo seeder and `lq worker --once`)."""
    out = []
    for _ in range(max_jobs):
        r = run_one(worker_id)
        if r is None:
            break
        out.append(r)
    return out


def run_forever() -> None:
    from lq.services import replenish

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = get_settings()
    wid = worker_name()
    log.info("worker %s started", wid)
    last_replenish = 0.0
    while True:
        try:
            if time.time() - last_replenish > s.replenish_interval_sec:
                last_replenish = time.time()
                with session_factory()() as db:
                    made = replenish.run_all(db)
                    db.commit()
                if made:
                    log.info("auto-replenish queued %d job(s)", made)
            if run_one(wid) is None:
                time.sleep(s.worker_poll_sec)
        except KeyboardInterrupt:
            break
        except Exception:
            log.error("worker loop error: %s", traceback.format_exc())
            time.sleep(5)

"""One budget for every paid step (content, review, TTS, ASR, retries).

reserve -> settle(actual) | mark_unknown | release. A reservation is its own committed transaction, so
money that may have been spent stays counted even if the job later fails. Paid use is OFF by default and
an API key alone never enables it. The cap limits what this app *starts*; it is not a billing guarantee."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import func, select

from lq.db import models as m
from lq.db.base import session_factory

DEFAULT_SYSTEM = {
    "paid_enabled": False,
    "daily_budget_usd": 1.0,
    "monthly_budget_usd": 15.0,  # calendar month (UTC); a second ceiling above the daily one
    "per_job_cap_usd": 0.5,
    "max_concurrent_jobs": 1,
    "auto_replenish": False,
    "replenish_modes": [],  # empty = the learner's active mode only
    "tts_accent": "",  # "" (voice default) | american | british | australian | mixed
    # one-off live verification: exactly this job may spend up to cap_usd even while paid_enabled is off
    "verification": None,  # {"job_id": str, "cap_usd": float, "started_at": iso}
    "last_verification_job_id": None,
}


class BudgetBlocked(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@lru_cache
def price_table() -> dict:
    return yaml.safe_load((Path(__file__).parent / "prices.yaml").read_text(encoding="utf-8"))


def get_system(session, realm: str, *, lock: bool = False) -> dict:
    q = select(m.SystemSetting).where(m.SystemSetting.realm == realm)
    row = session.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if row is None:
        row = m.SystemSetting(realm=realm, data=dict(DEFAULT_SYSTEM))
        session.add(row)
        session.flush()
    return {**DEFAULT_SYSTEM, **(row.data or {})}


def set_system(session, realm: str, patch: dict) -> dict:
    cur = get_system(session, realm, lock=True)
    allowed = {k: v for k, v in patch.items() if k in DEFAULT_SYSTEM}
    for k in ("daily_budget_usd", "per_job_cap_usd", "monthly_budget_usd"):
        if k in allowed and not (0 <= float(allowed[k]) <= (10000 if k == "monthly_budget_usd" else 1000)):
            raise ValueError(f"{k} out of range")
    if "tts_accent" in allowed and allowed["tts_accent"] not in ("", "american", "british", "australian", "mixed"):
        raise ValueError("unknown accent")
    if "max_concurrent_jobs" in allowed and not (1 <= int(allowed["max_concurrent_jobs"]) <= 8):
        raise ValueError("max_concurrent_jobs out of range")
    cur.update(allowed)
    row = session.get(m.SystemSetting, realm)
    row.data = cur
    row.updated_at = datetime.now(UTC)
    return cur


def _counted(e: m.CostEntry) -> float:
    if e.state in ("settled", "computed") and e.actual_usd is not None:
        return e.actual_usd
    if e.state == "released":
        return 0.0
    return e.estimated_usd  # reserved / unknown: assume it was spent


def spent_today(session, realm: str, day=None) -> dict:
    day = day or datetime.now(UTC).date()
    rows = (
        session.execute(select(m.CostEntry).where(m.CostEntry.realm == realm, m.CostEntry.budget_day == day))
        .scalars()
        .all()
    )
    out = {"settled_usd": 0.0, "reserved_usd": 0.0, "unknown_usd": 0.0, "counted_usd": 0.0, "day": day.isoformat()}
    for e in rows:
        out["counted_usd"] += _counted(e)
        if e.state in ("settled", "computed"):
            out["settled_usd"] += e.actual_usd or 0.0
        elif e.state == "reserved":
            out["reserved_usd"] += e.estimated_usd
        elif e.state == "unknown":
            out["unknown_usd"] += e.estimated_usd
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in out.items()}


def spent_month(session, realm: str, day=None) -> dict:
    """Spend counted in the calendar month (UTC) that contains `day`, in the same terms as spent_today."""
    day = day or datetime.now(UTC).date()
    first = day.replace(day=1)
    rows = (
        session.execute(
            select(m.CostEntry).where(
                m.CostEntry.realm == realm, m.CostEntry.budget_day >= first, m.CostEntry.budget_day <= day
            )
        )
        .scalars()
        .all()
    )
    out = {"settled_usd": 0.0, "reserved_usd": 0.0, "unknown_usd": 0.0, "counted_usd": 0.0}
    for e in rows:
        out["counted_usd"] += _counted(e)
        if e.state in ("settled", "computed"):
            out["settled_usd"] += e.actual_usd or 0.0
        elif e.state == "reserved":
            out["reserved_usd"] += e.estimated_usd
        elif e.state == "unknown":
            out["unknown_usd"] += e.estimated_usd
    return {**{k: round(v, 6) for k, v in out.items()}, "month": first.strftime("%Y-%m")}


class BudgetMeter:
    """Passed to paid providers. Free providers (demo) use FreeMeter."""

    paid = True

    def __init__(self, realm: str, job_id: str | None):
        self.realm = realm
        self.job_id = job_id

    def reserve(self, step: str, provider: str, model: str, estimated_usd: float | None) -> str:
        if estimated_usd is None:
            raise BudgetBlocked("price_unknown", f"no price entry for {provider}:{model}; refusing to treat it as free")
        table = price_table()
        with session_factory()() as s:
            cfg = get_system(s, self.realm, lock=True)  # serialises concurrent reservations per realm
            ver = cfg.get("verification") or {}
            if self.job_id and ver.get("job_id") == self.job_id:
                # verification run: one cap for everything this job does (content, review, speech, ASR, retries)
                rows = s.execute(select(m.CostEntry).where(m.CostEntry.job_id == self.job_id)).scalars().all()
                job_used = sum(_counted(e) for e in rows)
                if job_used + estimated_usd > float(ver["cap_usd"]) + 1e-9:
                    raise BudgetBlocked(
                        "verification_cap",
                        f"verification cap {ver['cap_usd']} USD would be exceeded (used {job_used:.4f}, need {estimated_usd:.4f})",
                    )
                e = m.CostEntry(realm=self.realm, job_id=self.job_id, step=step, provider=provider, model=model, state="reserved", budget_day=datetime.now(UTC).date(),
                                estimated_usd=float(estimated_usd), price_source=table["source"], price_checked_at=table["checked_at"])  # fmt: skip
                s.add(e)
                s.commit()
                return e.id
            if not cfg["paid_enabled"]:
                raise BudgetBlocked("paid_disabled", "paid generation is turned off in settings")
            day = datetime.now(UTC).date()
            used = spent_today(s, self.realm, day)["counted_usd"]
            if used + estimated_usd > float(cfg["daily_budget_usd"]) + 1e-9:
                raise BudgetBlocked(
                    "budget",
                    f"daily budget {cfg['daily_budget_usd']} USD would be exceeded (used {used:.4f}, need {estimated_usd:.4f})",
                )
            monthly = cfg.get("monthly_budget_usd")
            if monthly is not None:
                used_m = spent_month(s, self.realm, day)["counted_usd"]
                if used_m + estimated_usd > float(monthly) + 1e-9:
                    raise BudgetBlocked(
                        "monthly_budget",
                        f"monthly budget {monthly} USD would be exceeded (used {used_m:.4f}, need {estimated_usd:.4f})",
                    )
            if self.job_id:
                rows = s.execute(select(m.CostEntry).where(m.CostEntry.job_id == self.job_id)).scalars().all()
                job_used = sum(_counted(e) for e in rows)
                if job_used + estimated_usd > float(cfg["per_job_cap_usd"]) + 1e-9:
                    raise BudgetBlocked("job_cap", f"per-job cap {cfg['per_job_cap_usd']} USD would be exceeded")
            e = m.CostEntry(realm=self.realm, job_id=self.job_id, step=step, provider=provider, model=model, state="reserved", budget_day=day,
                            estimated_usd=float(estimated_usd), price_source=table["source"], price_checked_at=table["checked_at"])  # fmt: skip
            s.add(e)
            s.commit()
            return e.id

    def _update(self, entry_id: str, **kw) -> None:
        with session_factory()() as s:
            e = s.get(m.CostEntry, entry_id)
            for k, v in kw.items():
                setattr(e, k, v)
            s.commit()

    def settle(self, entry_id: str, actual_usd: float | None, usage: dict) -> None:
        """actual_usd None = the API answered but cost could not be derived from usage -> unknown."""
        if actual_usd is None:
            self._update(entry_id, state="unknown", usage=usage)
        else:
            self._update(entry_id, state="settled", actual_usd=float(actual_usd), usage=usage)

    def computed(self, entry_id: str, usd: float, basis: dict) -> None:
        """The API answered but returned no billable usage: cost computed here from list prices and what we
        measured (audio length, characters). An estimate, not the provider's figure."""
        self._update(entry_id, state="computed", actual_usd=float(usd), usage=basis)

    def mark_unknown(self, entry_id: str, why: str) -> None:
        """Timeout / dropped connection: we cannot know whether we were billed. Keep the estimate counted."""
        self._update(entry_id, state="unknown", usage={"unknown_reason": why})

    def release(self, entry_id: str) -> None:
        """Only when the request provably never left this process."""
        self._update(entry_id, state="released")


class FreeMeter:
    paid = False

    def reserve(self, *a, **k) -> str:
        return ""

    def settle(self, *a, **k) -> None: ...
    def computed(self, *a, **k) -> None: ...
    def mark_unknown(self, *a, **k) -> None: ...
    def release(self, *a, **k) -> None: ...


def cost_summary(rows) -> dict:
    """settled = from usage the API returned; computed = API answered without usage, priced here from
    measurements; unknown = no answer (timeout, dropped connection), counted at the pre-request estimate;
    reserved = in flight. counted = what the budget charges (all of the above)."""
    return {
        "settled_usd": round(sum(e.actual_usd or 0 for e in rows if e.state == "settled"), 6),
        "computed_usd": round(sum(e.actual_usd or 0 for e in rows if e.state == "computed"), 6),
        "unknown_usd": round(sum(e.estimated_usd for e in rows if e.state == "unknown"), 6),
        "reserved_usd": round(sum(e.estimated_usd for e in rows if e.state == "reserved"), 6),
        "counted_usd": round(sum(_counted(e) for e in rows), 6),
        "pre_estimates_usd": round(sum(e.estimated_usd for e in rows), 6),
        "requests": len(rows),
    }


def job_cost(session, job_id: str) -> dict:
    return cost_summary(session.execute(select(m.CostEntry).where(m.CostEntry.job_id == job_id)).scalars().all())


def running_jobs(session, realm: str, now) -> int:
    return session.execute(
        select(func.count())
        .select_from(m.GenerationJob)
        .where(
            m.GenerationJob.realm == realm, m.GenerationJob.status == "RUNNING", m.GenerationJob.lease_expires_at > now
        )
    ).scalar_one()


# ---- estimators (None = unknown price)


def est_text(model: str, input_chars: int, max_output_tokens: int) -> float | None:
    t = price_table()
    p = t["text"].get(model)
    if not p:
        return None
    return (input_chars / t["assumptions"]["chars_per_text_token"] * p["input"] + max_output_tokens * p["output"]) / 1e6


def cost_text(model: str, usage: dict) -> float | None:
    p = price_table()["text"].get(model)
    if not p or "input_tokens" not in usage or "output_tokens" not in usage:
        return None
    return (usage["input_tokens"] * p["input"] + usage["output_tokens"] * p["output"]) / 1e6


def est_tts(kind: str, model: str, text: str) -> float | None:
    t = price_table()
    p = t["realtime" if kind == "realtime" else "speech"].get(model)
    if not p:
        return None
    a = t["assumptions"]
    seconds = max(1.0, len(text) / a["speech_chars_per_second"]) * 1.3
    return (
        len(text) / a["chars_per_text_token"] * 1.5 * p["text_input"]
        + seconds * a["audio_output_tokens_per_second"] * p["audio_output"]
    ) / 1e6 + 0.0005


def cost_realtime(model: str, usage: dict) -> float | None:
    p = price_table()["realtime"].get(model)
    try:
        i, o = usage["input_token_details"], usage["output_token_details"]
        return (
            i.get("text_tokens", 0) * p["text_input"]
            + i.get("audio_tokens", 0) * p["audio_input"]
            + o.get("text_tokens", 0) * p["text_output"]
            + o.get("audio_tokens", 0) * p["audio_output"]
        ) / 1e6
    except (KeyError, TypeError):
        return None


def est_asr(model: str, seconds: float) -> float | None:
    p = price_table()["transcription"].get(model)
    return None if p is None else max(seconds, 1.0) / 60.0 * p


def cost_tts_measured(kind: str, model: str, text: str, seconds: float) -> float | None:
    """Price a speech request from the text sent and the audio length actually received (no usage returned)."""
    t = price_table()
    p = t["realtime" if kind == "realtime" else "speech"].get(model)
    if not p:
        return None
    a = t["assumptions"]
    return (
        len(text) / a["chars_per_text_token"] * p["text_input"]
        + seconds * a["audio_output_tokens_per_second"] * p["audio_output"]
    ) / 1e6


def estimate_unit(mode: str, part: str, settings) -> dict:
    """Pre-run estimate (upper side) for one unit with the configured models. None = unknown price."""
    import json as _json

    from lq.audio.render import plan_clips
    from lq.generator import authoring
    from lq.generator.providers import GenSpec
    from lq.services.seed import load_fixture_units

    prof = __import__("lq.domain.profiles", fromlist=["x"])
    part_spec = prof.part_spec(prof.current_profile(mode), part)
    system, user = authoring.build_generation_prompt(
        GenSpec(mode=mode, part=part, template_type=part_spec["template_type"], primary_skill="explicit_detail", band=3)
    )
    words = authoring.templates()["modes"][mode][part]["words"][1]
    sample = next((u for u in load_fixture_units() if u.mode == mode and u.part == part), None)
    clips = len(plan_clips(sample)) if sample else 10
    chars = words * 6 + clips * 60
    speech = settings.tts_provider == "openai_speech"
    est = {
        "content": est_text(
            settings.content_model, len(system) + len(user) + len(_json.dumps(authoring.authored_schema())), 6000
        ),
        "review": est_text(settings.review_model, words * 8 + 1500, 2500),
        "tts": est_tts(
            "speech" if speech else "realtime", settings.tts_speech_model if speech else settings.tts_model, "x" * chars
        ),
        "asr": est_asr(settings.asr_model, chars / 14),
    }
    total = None if any(v is None for v in est.values()) else sum(est.values())
    return {
        "by_step": {k: (round(v, 4) if v is not None else None) for k, v in est.items()},
        "total_usd": None if total is None else round(total, 4),
        "clips": clips,
    }

"""Automatic stock refill. Triggered periodically by the worker - never per answer. Next-unit choice
always comes from READY stock; this only queues generation when untouched stock runs low."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from lq.db import models as m
from lq.domain import rules
from lq.domain.profiles import current_profile
from lq.generator import budget
from lq.jobs import queue
from lq.services import learning as L
from lq.services import plans

ACTIVE = ("QUEUED", "RUNNING", "RETRY_WAIT", "BLOCKED")
SKILL_ROTATION = ("explicit_detail", "numbers_negation", "inference", "speaker_attitude", "main_idea", "discourse_structure", "condition_matching", "integration")


def inventory(session, user: m.User, mode: str, now: datetime) -> dict:
    today = L.user_day(user, now)
    cands, state, _ = plans.learner_context(session, user, mode, today, now)
    untouched = [c for c in cands if not c.touched]
    profile = current_profile(mode)
    by_part = {p["part"]: {"part": p["part"], "label_ja": p["label_ja"], "units": 0, "sec": 0} for p in profile["parts"]}
    for c in untouched:
        if c.part in by_part:
            by_part[c.part]["units"] += 1
            by_part[c.part]["sec"] += c.est_sec
    J = m.GenerationJob
    inflight = session.execute(select(J).where(J.realm == user.realm, J.kind == "generate", J.status.in_(ACTIVE))).scalars().all()
    pending = [j for j in inflight if j.params.get("mode") == mode and j.params.get("pool", "practice") == "practice"]
    daily = int(L.user_settings(user)["daily_goal_minutes"]) * 60
    sec = sum(c.est_sec for c in untouched)
    return {"mode": mode, "untouched_units": len(untouched), "untouched_sec": sec, "days_of_stock": round(sec / daily, 2) if daily else None, "by_part": list(by_part.values()),
            "jobs_in_progress": len(pending), "state": state, "typical_sec": {p: (v["sec"] // v["units"] if v["units"] else None) for p, v in by_part.items()}}  # fmt: skip


def plan_jobs(session, user: m.User, mode: str, now: datetime) -> list[dict]:
    """Which generation specs to queue for this mode: below `low_days` of untouched stock, top up toward
    `target_days`, favouring formats with the least stock and the learner's weak / unmeasured skills."""
    cfg = rules.load_config()
    inv = inventory(session, user, mode, now)
    daily = int(L.user_settings(user)["daily_goal_minutes"]) * 60
    if inv["untouched_sec"] >= cfg.replenish["low_days"] * daily:
        return []
    profile = current_profile(mode)
    state = inv["state"]
    from lq.domain.recommend import target_skills

    skills = [s for s, _ in target_skills(state, cfg)]
    skills += [s for s in SKILL_ROTATION if s not in skills and state.skill_stats.get(s, (0, None))[0] < cfg.skill_min_observations]
    skills += [s for s in SKILL_ROTATION if s not in skills]
    typical = [v for v in inv["typical_sec"].values() if v] or [90]
    need = cfg.replenish["target_days"] * daily - inv["untouched_sec"] - inv["jobs_in_progress"] * (sum(typical) // len(typical))  # queued work counts as stock on the way
    stock = {p["part"]: p["units"] for p in inv["by_part"]}
    specs, i = [], 0
    while need > 0 and len(specs) < 60:
        part = min(profile["parts"], key=lambda p: (stock[p["part"]], profile["parts"].index(p)))  # least-stocked format first
        est = inv["typical_sec"].get(part["part"]) or (45 + 40 * part["questions_per_unit"])
        skill = "integration" if part["template_type"] in ("lecture_integrated", "condition_choice") else skills[i % len(skills)]
        specs.append({"mode": mode, "part": part["part"], "template_type": part["template_type"], "primary_skill": skill, "band": state.band, "source": "generated", "pool": "practice"})
        stock[part["part"]] += 1
        need -= est
        i += 1
    return specs


def run_for_user(session, user: m.User, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    cfg_sys = budget.get_system(session, user.realm)
    if not cfg_sys["auto_replenish"]:
        return 0
    if user.realm != "demo" and not cfg_sys["paid_enabled"]:
        return 0  # an API key alone never starts paid work
    cfg = rules.load_config()
    day = now.date().isoformat()
    J = m.GenerationJob
    made_today = session.execute(select(func.count()).select_from(J).where(J.realm == user.realm, J.dedupe_key.like(f"replenish:{user.realm}:%:{day}:%"))).scalar_one()
    modes = cfg_sys["replenish_modes"] or [L.user_settings(user)["active_mode"]]  # never all five unconditionally
    made = 0
    for mode in modes:
        for spec in plan_jobs(session, user, mode, now):
            if made_today + made >= cfg.replenish["max_jobs_per_day"]:
                return made
            n = made_today + made
            spec["seed"] = f"auto-{day}-{n}"
            _, created = queue.enqueue(session, user.realm, "generate", spec, dedupe_key=f"replenish:{user.realm}:{mode}:{day}:{n}")
            made += 1 if created else 0
    return made


def run_all(session) -> int:
    return sum(run_for_user(session, u) for u in session.execute(select(m.User)).scalars())

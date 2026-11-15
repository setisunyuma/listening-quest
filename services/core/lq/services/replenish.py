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
SKILL_ROTATION = (
    "explicit_detail",
    "numbers_negation",
    "inference",
    "speaker_attitude",
    "main_idea",
    "discourse_structure",
    "condition_matching",
    "integration",
)


def inventory(session, user: m.User, mode: str, now: datetime) -> dict:
    today = L.user_day(user, now)
    cands, state, _ = plans.learner_context(session, user, mode, today, now)
    untouched = [c for c in cands if not c.touched]
    profile = current_profile(mode)
    by_part = {
        p["part"]: {"part": p["part"], "label_ja": p["label_ja"], "units": 0, "sec": 0} for p in profile["parts"]
    }
    for c in untouched:
        if c.part in by_part:
            by_part[c.part]["units"] += 1
            by_part[c.part]["sec"] += c.est_sec
    J = m.GenerationJob
    inflight = (
        session.execute(select(J).where(J.realm == user.realm, J.kind == "generate", J.status.in_(ACTIVE)))
        .scalars()
        .all()
    )
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
    skills += [
        s
        for s in SKILL_ROTATION
        if s not in skills and state.skill_stats.get(s, (0, None))[0] < cfg.skill_min_observations
    ]
    skills += [s for s in SKILL_ROTATION if s not in skills]
    typical = [v for v in inv["typical_sec"].values() if v] or [90]
    need = (
        cfg.replenish["target_days"] * daily
        - inv["untouched_sec"]
        - inv["jobs_in_progress"] * (sum(typical) // len(typical))
    )  # queued work counts as stock on the way
    stock = {p["part"]: p["units"] for p in inv["by_part"]}
    specs, i = [], 0
    while need > 0 and len(specs) < 60:
        part = min(
            profile["parts"], key=lambda p: (stock[p["part"]], profile["parts"].index(p))
        )  # least-stocked format first
        est = inv["typical_sec"].get(part["part"]) or (45 + 40 * part["questions_per_unit"])
        skill = (
            "integration"
            if part["template_type"] in ("lecture_integrated", "condition_choice")
            else skills[i % len(skills)]
        )
        specs.append(
            {
                "mode": mode,
                "part": part["part"],
                "template_type": part["template_type"],
                "primary_skill": skill,
                "band": state.band,
                "source": "generated",
                "pool": "practice",
            }
        )
        stock[part["part"]] += 1
        need -= est
        i += 1
    return specs


PRACTICE_MIN_UNSEEN = 2  # unseen tasks kept ready per open practice kind (a small stock, not every stage)


def practice_jobs(session, user: m.User) -> list[dict]:
    """Generation jobs for the practice kinds open to this learner: each kind is topped up to a small unseen stock,
    and each job asks for the scene of that kind used least so far, so unlock evidence (different material, several
    scenes) can actually be collected and no single kind or scene takes over."""
    from lq.domain.practice import task_def
    from lq.services import practice

    J, U = m.GenerationJob, m.Unit
    inflight = [
        j for j in session.execute(select(J).where(J.realm == user.realm, J.status.in_(("QUEUED", "RUNNING", "RETRY_WAIT", "BLOCKED")))).scalars()
        if (j.params or {}).get("practice_kind")
    ]  # fmt: skip
    out = []
    for k in practice.available_kinds(session, user):
        t = task_def(k)
        mine = [j for j in inflight if j.params["practice_kind"] == k]
        unseen = practice.unseen_count(session, user, k)
        need = PRACTICE_MIN_UNSEEN - unseen - len(mine)
        if need <= 0:
            continue
        used: dict[str, int] = {sc["id"]: 0 for sc in t.get("scene_pool", [])}
        for u in session.execute(
            select(U).where(U.realm == user.realm, U.template_type == "practice_task", U.part == k)
        ).scalars():
            sc = ((u.content or {}).get("practice") or {}).get("scene")
            if sc in used:
                used[sc] += 1
        for j in mine:
            sc = ((j.params or {}).get("extra") or {}).get("scene")
            if sc in used:
                used[sc] += 1
        for _ in range(need):
            scene = min(used, key=lambda x: (used[x], x)) if used else ""
            if scene:
                used[scene] += 1
            ja = next((sc["ja"] for sc in t.get("scene_pool", []) if sc["id"] == scene), "")
            out.append({"mode": t["stage"], "part": k, "template_type": "practice_task", "primary_skill": t["prereq_skills"][0], "band": 3,
                        "source": "generated", "pool": "practice", "practice_kind": k, "extra": {"scene": scene, "scene_ja": ja}})  # fmt: skip
    return out


def run_for_user(session, user: m.User, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    cfg_sys = budget.get_system(session, user.realm)
    if not cfg_sys["auto_replenish"]:
        return 0
    if user.realm != "demo" and not cfg_sys["paid_enabled"]:
        return 0  # an API key alone never starts paid work
    cfg = rules.load_config()
    day = budget.budget_day(now).isoformat()  # the same day as the budget: Japan time, 04:00
    J = m.GenerationJob
    made_today = session.execute(
        select(func.count())
        .select_from(J)
        .where(J.realm == user.realm, J.dedupe_key.like(f"replenish:{user.realm}:%:{day}:%"))
    ).scalar_one()
    modes = cfg_sys["replenish_modes"] or [L.user_settings(user)["active_mode"]]  # never all five unconditionally
    made = 0
    # T01 / Q01: practice tasks get a minimum stock only for the kinds open to this learner (never all stages)
    per_kind: dict[str, int] = {}
    for spec in practice_jobs(session, user):
        if made_today + made >= cfg.replenish["max_jobs_per_day"]:
            return made
        k = spec["practice_kind"]
        per_kind[k] = per_kind.get(k, 0) + 1
        _, created = queue.enqueue(session, user.realm, "generate", {**spec, "seed": f"auto-{day}-{k}-{per_kind[k]}-{spec['extra']['scene']}"},
                                   dedupe_key=f"replenish:{user.realm}:{k}:{day}:p{per_kind[k]}")  # fmt: skip
        made += 1 if created else 0

    for mode in modes:
        for spec in plan_jobs(session, user, mode, now):
            if made_today + made >= cfg.replenish["max_jobs_per_day"]:
                return made
            n = made_today + made
            spec["seed"] = f"auto-{day}-{n}"
            _, created = queue.enqueue(
                session, user.realm, "generate", spec, dedupe_key=f"replenish:{user.realm}:{mode}:{day}:{n}"
            )
            made += 1 if created else 0
    return made


def run_all(session) -> int:
    return sum(run_for_user(session, u) for u in session.execute(select(m.User)).scalars())

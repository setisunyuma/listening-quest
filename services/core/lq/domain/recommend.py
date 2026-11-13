"""Deterministic unit selection and daily planning. Pure functions: same inputs + seed -> same output."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date

from lq.domain.content import SKILL_LABELS_JA
from lq.domain.rules import LearningConfig


@dataclass(frozen=True)
class Candidate:
    unit_id: str
    mode: str
    part: str
    template_type: str
    skills: tuple[str, ...]
    primary_skill: str
    topic: str
    band: int
    est_sec: int
    family_id: str
    fingerprint: str
    touched: bool = False  # any exposure to this script/audio
    family_touched: bool = False
    due_day: date | None = None  # recall schedule
    stage: int | None = None
    last_result: str | None = None


@dataclass(frozen=True)
class LearnerState:
    mode: str
    band: int
    today: date
    need_skills: tuple[str, ...] = ()  # skills missed / assisted recently, most recent first
    skill_stats: dict = field(default_factory=dict)  # skill -> (n, mean) from strict-first observations
    recent_templates: tuple[str, ...] = ()  # most recent first
    recent_topics: tuple[str, ...] = ()
    recent_skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pick:
    unit_id: str
    category: str  # review | weak | explore
    est_sec: int
    reason_code: str
    reason_text: str
    params: dict


def tiebreak(seed: str, unit_id: str) -> int:
    return int(hashlib.sha256(f"{seed}:{unit_id}".encode()).hexdigest()[:12], 16)


def _recency(value: str, recent: tuple[str, ...]) -> int:
    """0 = never seen recently (best for range), larger = more recent."""
    return len(recent) - recent.index(value) if value in recent else 0


def target_skills(state: LearnerState, cfg: LearningConfig) -> list[tuple[str, str]]:
    """(skill, why). Recent misses first (usable with few observations); then measured weak skills."""
    out: list[tuple[str, str]] = []
    for s in state.need_skills:
        if s not in [x for x, _ in out]:
            out.append((s, "recent_miss"))
    measured = [
        (mean, s)
        for s, (n, mean) in sorted(state.skill_stats.items())
        if n >= cfg.skill_min_observations and mean is not None and mean < cfg.skill_weak_threshold
    ]
    for _mean, s in sorted(measured):
        if s not in [x for x, _ in out]:
            out.append((s, "measured_weak"))
    return out


def pick_review(cands, state: LearnerState, exclude: set[str], cap_sec: int | None, seed: str) -> Pick | None:
    due = [
        c
        for c in cands
        if c.unit_id not in exclude
        and c.due_day is not None
        and c.due_day <= state.today
        and (cap_sec is None or c.est_sec <= cap_sec)
    ]
    if not due:
        return None
    due.sort(
        key=lambda c: (
            0 if c.last_result == "incorrect" else 1,
            -(state.today - c.due_day).days,
            tiebreak(seed, c.unit_id),
        )
    )
    c = due[0]
    overdue = (state.today - c.due_day).days
    text = (
        "誤答した問題の復習"
        if c.last_result == "incorrect"
        else ("期限が来た復習" if overdue == 0 else f"期限を{overdue}日過ぎた復習")
    )
    return Pick(
        c.unit_id,
        "review",
        c.est_sec,
        "review_due",
        text,
        {"due_day": c.due_day.isoformat(), "stage": c.stage, "overdue_days": overdue, "last_result": c.last_result},
    )


def pick_weak(
    cands,
    state: LearnerState,
    exclude: set[str],
    cap_sec: int | None,
    seed: str,
    cfg: LearningConfig,
    skip_skills: tuple[str, ...] = (),
) -> Pick | None:
    targets = [(s, why) for s, why in target_skills(state, cfg) if s not in skip_skills] or target_skills(state, cfg)
    if not targets:
        return None
    rank = {s: i for i, (s, _) in enumerate(targets)}
    why_of = dict(targets)
    pool = []
    for c in cands:
        if c.unit_id in exclude or c.touched or (cap_sec is not None and c.est_sec > cap_sec):
            continue
        hit = [s for s in c.skills if s in rank]
        if not hit:
            continue
        best = min(hit, key=lambda s: rank[s])
        pool.append(
            (rank[best], 1 if c.family_touched else 0, abs(c.band - state.band), tiebreak(seed, c.unit_id), c, best)
        )
    if not pool:
        return None
    pool.sort(key=lambda t: t[:4])
    _, _, _, _, c, skill = pool[0]
    label = SKILL_LABELS_JA.get(skill, skill)
    text = f"{label}を再確認" if why_of[skill] == "recent_miss" else f"{label}を重点練習"
    return Pick(
        c.unit_id,
        "weak",
        c.est_sec,
        "weak_skill",
        text,
        {
            "skill": skill,
            "why": why_of[skill],
            "band": c.band,
            "learner_band": state.band,
            "transfer_family": c.family_touched,
        },
    )


def pick_explore(
    cands, state: LearnerState, exclude: set[str], cap_sec: int | None, seed: str, cfg: LearningConfig
) -> Pick | None:
    pool = []
    for c in cands:
        if c.unit_id in exclude or c.touched or (cap_sec is not None and c.est_sec > cap_sec):
            continue
        unmeasured = sum(1 for s in c.skills if state.skill_stats.get(s, (0, None))[0] < cfg.skill_min_observations)
        pool.append(
            (
                1 if c.family_touched else 0,
                _recency(c.template_type, state.recent_templates),
                -unmeasured,
                _recency(c.primary_skill, state.recent_skills),
                _recency(c.topic, state.recent_topics),
                abs(c.band - state.band),
                tiebreak(seed, c.unit_id),
                c,
            )
        )
    if not pool:
        return None
    pool.sort(key=lambda t: t[:7])
    c = pool[0][-1]
    n_primary = state.skill_stats.get(c.primary_skill, (0, None))[0]
    if n_primary == 0:
        text = f"未測定の「{SKILL_LABELS_JA.get(c.primary_skill, c.primary_skill)}」を広げる"
    elif c.template_type not in state.recent_templates:
        text = "最近出ていない形式で範囲を維持"
    else:
        text = "初見の教材で範囲を広げる"
    return Pick(
        c.unit_id,
        "explore",
        c.est_sec,
        "explore_range",
        text,
        {
            "template_type": c.template_type,
            "primary_skill": c.primary_skill,
            "band": c.band,
            "learner_band": state.band,
        },
    )


def pick_for(category: str, cands, state, exclude, cap_sec, seed, cfg, skip_skills=()) -> Pick | None:
    """Pick for a slot of `category`, falling back to the categories that widen coverage."""
    order = {
        "review": ("review", "weak", "explore"),
        "weak": ("weak", "explore", "review"),
        "explore": ("explore", "weak", "review"),
    }[category]
    for cat in order:
        if cat == "review":
            p = pick_review(cands, state, exclude, cap_sec, seed)
        elif cat == "weak":
            p = pick_weak(cands, state, exclude, cap_sec, seed, cfg, skip_skills)
        else:
            p = pick_explore(cands, state, exclude, cap_sec, seed, cfg)
        if p:
            return p
    return None


def slot_cap(est_sec: int, cfg: LearningConfig) -> int:
    return int(math.ceil(est_sec * cfg.slot_cap_factor / 30.0) * 30)


@dataclass
class PlanDraft:
    slots: list[dict]
    planned_sec: int
    budget_sec: int
    notes: list[str]


def _advance(state: LearnerState, c: Candidate) -> LearnerState:
    return LearnerState(
        mode=state.mode,
        band=state.band,
        today=state.today,
        need_skills=state.need_skills,
        skill_stats=state.skill_stats,
        recent_templates=(c.template_type, *[t for t in state.recent_templates if t != c.template_type]),
        recent_topics=(c.topic, *[t for t in state.recent_topics if t != c.topic]),
        recent_skills=(c.primary_skill, *[t for t in state.recent_skills if t != c.primary_skill]),
    )


def build_plan(
    cands: list[Candidate],
    state: LearnerState,
    budget_sec: int,
    cfg: LearningConfig,
    seed: str,
    exclude: set[str] | None = None,
) -> PlanDraft:
    """Fill the time budget: due reviews 40%, weak-skill new units 40%, range-widening new units 20%.
    Unused quota (no due reviews / no weak signal) flows into the next category, ending in exploration.
    A slot is one whole unit; units are never cut to fit."""
    by_id = {c.unit_id: c for c in cands}
    used: set[str] = set(exclude or ())
    slots: list[dict] = []
    total = 0
    cur = state
    carry = 0.0
    picked_skills: tuple[str, ...] = ()

    def fill(category: str, quota: float) -> float:
        nonlocal total, cur, picked_skills
        spent = 0.0
        while True:
            room = min(quota - spent, budget_sec - total)
            if room <= 0:
                break
            cap_est = int(room) if slots else None  # the very first unit may exceed a small budget
            if category == "review":
                p = pick_review(cands, cur, used, cap_est, seed)
            elif category == "weak":
                p = pick_weak(cands, cur, used, cap_est, seed, cfg, picked_skills)
            else:
                p = pick_explore(cands, cur, used, cap_est, seed, cfg)
            if p is None:
                break
            used.add(p.unit_id)
            if category == "weak":
                picked_skills = (*picked_skills, p.params["skill"])
                if len(picked_skills) >= len(target_skills(cur, cfg)):
                    picked_skills = ()
            slots.append(
                {
                    "category": p.category,
                    "unit_id": p.unit_id,
                    "est_sec": p.est_sec,
                    "time_cap_sec": slot_cap(p.est_sec, cfg),
                    "reason_code": p.reason_code,
                    "reason_text": p.reason_text,
                    "reason_params": p.params,
                }
            )
            total += p.est_sec
            spent += p.est_sec
            cur = _advance(cur, by_id[p.unit_id])
        return max(0.0, quota - spent)

    for category in ("review", "weak", "explore"):
        carry = fill(category, budget_sec * cfg.plan_split[category] + carry)
    # leftover budget: more exploration, then more due reviews (never invent slots beyond the budget)
    for category in ("explore", "weak", "review"):
        if budget_sec - total > 0:
            fill(category, budget_sec - total)
    notes: list[str] = []
    if not slots:
        notes.append("no_ready_units")
    elif total < budget_sec * 0.6:
        notes.append("short_inventory")
    return PlanDraft(slots=slots, planned_sec=total, budget_sec=budget_sec, notes=notes)

"""Pure progression rules (統合実装指示書 G01 / P01). No DB access, no clocks: callers inject `today`.

Three independent decisions, each with its own function and its own tests:
- practice_unlock: may a practice task kind join the daily plan (G01). Never a certification.
- transition_readiness: should the app *propose* making the next stage the main goal (P01). Never automatic.
- select_inherited_reviews: which due reviews of earlier / other stages go into today's plan (P01).
The spaced-review intervals (rules.srs_next) and band adjustment (rules.adjust_band) are not used or changed here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache
def load_progression() -> dict:
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "progression.yaml").read_text(encoding="utf-8"))


def next_stage(mode: str, cfg: dict | None = None) -> str | None:
    stages = (cfg or load_progression())["stages"]
    i = stages.index(mode) if mode in stages else -1
    return stages[i + 1] if 0 <= i < len(stages) - 1 else None


# ---------------------------------------------------------------- observations


@dataclass(frozen=True)
class Obs:
    """One scored observation. kind: first (unaided first exposure under the stated conditions) | assisted |
    review | delayed (a later spaced check) | trial (a short try of another stage) | radio | self_report."""

    day: date
    mode: str
    skill: str
    score: float
    kind: str
    scene: str = ""
    family: str = ""
    critical: bool = False


SKILL_JA = {
    "explicit_detail": "明示された情報", "numbers_negation": "数字・否定", "inference": "推論", "speaker_attitude": "話者の意図・態度",
    "main_idea": "要点", "discourse_structure": "話の構成", "condition_matching": "条件の照合", "integration": "情報の統合",
}  # fmt: skip


def _skill_ja(s: str) -> str:
    return SKILL_JA.get(s, s)


def transition_readiness(mode: str, obs: Iterable[Obs], today: date, cfg: dict | None = None) -> dict:
    """P01: evidence for proposing the next stage as main goal. Only unaided first-exposure observations of the
    current goal count as "stable"; assisted, repeated, radio and self-reported results never replace them.
    Missing evidence is reported as "未確認", never as a lack of ability."""
    cfg = cfg or load_progression()
    t = cfg["transition"]
    nxt = next_stage(mode, cfg)
    start = today - timedelta(days=t["window_days"] - 1)
    obs = list(obs)
    recent = [o for o in obs if o.mode == mode and start <= o.day <= today]
    valid = [o for o in recent if o.kind == "first"]
    days = {o.day for o in valid}
    scenes = {o.scene for o in valid if o.scene}
    missing: list[dict] = []
    met: list[str] = []

    def need(ok: bool, code: str, ja: str, done_ja: str) -> None:
        (met.append(done_ja) if ok else missing.append({"code": code, "label_ja": ja}))

    need(
        len(days) >= t["min_days"],
        "days",
        f"{t['min_days']}日以上に分けた初見の回答（現在 {len(days)} 日）",
        f"{len(days)}日に分けて回答",
    )
    need(
        len(scenes) >= t["min_scenes"],
        "scenes",
        f"{t['min_scenes']}種類以上の形式・場面（現在 {len(scenes)}）",
        f"{len(scenes)}種類の形式・場面",
    )
    skills = {}
    for s in t["main_skills"].get(mode, []):
        vals = [o.score for o in valid if o.skill == s]
        n = len(vals)
        mean = sum(vals) / n if n else None
        if n < t["min_obs_per_skill"]:
            status = "unobserved"
            missing.append(
                {
                    "code": f"skill_unobserved:{s}",
                    "label_ja": f"「{_skill_ja(s)}」の初見の観測が不足（{n}/{t['min_obs_per_skill']}件・未確認）",
                }
            )
        elif mean < t["stable_score"]:
            status = "unstable"
            missing.append(
                {
                    "code": f"skill_unstable:{s}",
                    "label_ja": f"「{_skill_ja(s)}」が安定していない（最近の平均 {round(mean * 100)}%）",
                }
            )
        else:
            status = "stable"
            met.append(f"「{_skill_ja(s)}」が安定（{n}件・平均 {round(mean * 100)}%）")
        skills[s] = {
            "label_ja": _skill_ja(s),
            "n": n,
            "mean": None if mean is None else round(mean, 3),
            "status": status,
        }
    delayed = [o for o in obs if o.mode == mode and o.kind == "delayed" and o.score >= 1.0 and o.day <= today]
    need(
        len(delayed) >= t["min_delayed_checks"],
        "delayed",
        "時間を空けた確認で重要情報を再び扱えたこと（未確認）",
        "時間を空けた確認に正答",
    )
    crit_from = today - timedelta(days=t["critical_lookback_days"] - 1)
    crit = [o for o in obs if o.mode == mode and o.critical and crit_from <= o.day <= today]
    if crit:
        missing.append(
            {
                "code": "critical",
                "label_ja": f"最近の確認に重大な取り違え（主体・否定・数値・条件・話者・確実性）が {len(crit)} 件残っている",
            }
        )
    else:
        met.append("最近の確認に重大な取り違えなし")
    if nxt:
        trials = [o for o in obs if o.mode == nxt and o.kind in ("first", "assisted", "trial") and o.day <= today]
        tdays = {(o.day, o.family) for o in trials}
        need(
            len(tdays) >= t["min_next_stage_trials"],
            "next_trials",
            f"次の段階の未見の短い確認（{len(tdays)}/{t['min_next_stage_trials']}件）",
            f"次の段階を{len(tdays)}件体験",
        )
    return {
        "mode": mode, "next_mode": nxt, "ready": bool(nxt) and not missing, "met": met, "missing": missing, "skills": skills,
        "version": cfg["version"], "period": [start.isoformat(), today.isoformat()],
    }  # fmt: skip


# ---------------------------------------------------------------- G01 practice unlock


@dataclass(frozen=True)
class TaskEvidence:
    """One scored practice / exam task on the prerequisite skills. `eligible` is False for self reports, viewing
    explanations, radio, think-only answers, pending AI scoring, quarantined material or assisted runs that are
    used as unaided evidence."""

    day: date
    kind: str  # the task kind that produced it (its own rubric scale)
    family: str
    scene: str
    score: float  # mean of that kind's main rubric items, 0..1
    eligible: bool = True
    critical: bool = False


def practice_unlock(
    target: str, prereq_kinds: Iterable[str], evidence: Iterable[TaskEvidence], cfg: dict | None = None
) -> dict:
    """G01: recommend adding practice kind `target` to the daily plan. Evidence from different material families only
    (the same script / a small edit / the same audio never counts twice). Means are per task kind, never mixed."""
    cfg = cfg or load_progression()
    u = cfg["unlock"]
    prereq = set(prereq_kinds)
    ev = sorted((e for e in evidence if e.eligible and (not prereq or e.kind in prereq)), key=lambda e: e.day)
    by_family: dict[str, TaskEvidence] = {}
    for e in ev:
        by_family[e.family] = e  # latest per family
    uniq = list(by_family.values())
    if not uniq:
        return {
            "target": target,
            "status": "unknown",
            "missing": [{"code": "no_data", "label_ja": "まだ証拠がありません（未確認・能力不足ではありません）"}],
            "version": cfg["version"],
        }
    missing = []
    if len(uniq) < u["min_tasks"]:
        missing.append({"code": "tasks", "label_ja": f"異なる教材での有効な課題が不足（{len(uniq)}/{u['min_tasks']}）"})
    days = {e.day for e in uniq}
    if len(days) < u["min_days"]:
        missing.append({"code": "days", "label_ja": f"学習日の分散が不足（{len(days)}/{u['min_days']}日）"})
    scenes = {e.scene for e in uniq if e.scene}
    if len(scenes) < u["min_scenes"]:
        missing.append({"code": "scenes", "label_ja": f"場面・話者構成の種類が不足（{len(scenes)}/{u['min_scenes']}）"})
    per_kind: dict[str, list[float]] = {}
    for e in uniq:
        per_kind.setdefault(e.kind, []).append(e.score)
    means = {k: sum(v) / len(v) for k, v in per_kind.items()}
    for k, mval in means.items():
        if mval < u["min_mean"]:
            missing.append({"code": f"mean:{k}", "label_ja": f"{k} の主要項目の平均が目安未満（{round(mval * 100)}%）"})
    if any(e.critical for e in uniq[-3:]):
        missing.append({"code": "critical", "label_ja": "最近の回答に重大な取り違えがあるため、先に短い確認を挟みます"})
    return {
        "target": target, "status": "recommend" if not missing else "not_yet", "missing": missing,
        "means": {k: round(v, 3) for k, v in means.items()}, "tasks": len(uniq), "days": len(days), "version": cfg["version"],
    }  # fmt: skip


# ---------------------------------------------------------------- P01 allocation and review inheritance


def plan_split(budget_sec: int, in_transition: bool, cfg: dict | None = None) -> dict:
    """Seconds for new-stage work / due reviews from every stage / bridge checks. A starting example only."""
    cfg = cfg or load_progression()
    if in_transition:
        sp = cfg["transition_period"]["split"]
        out = {
            "new": int(budget_sec * sp["new"]),
            "review": int(budget_sec * sp["review"]),
            "bridge": int(budget_sec * sp["bridge"]),
        }
    else:
        r = int(budget_sec * cfg["maintenance"]["review_share"])
        out = {"new": budget_sec - r, "review": r, "bridge": 0}
    out["short_day"] = budget_sec <= cfg["transition_period"]["short_day_sec"]
    return out


@dataclass(frozen=True)
class DueReview:
    unit_id: str
    mode: str
    due_day: date
    est_sec: int
    skill: str
    family: str
    last_result: str | None = None
    available: bool = True
    unavailable_reason: str = ""


def select_inherited_reviews(
    due: Iterable[DueReview], cap_sec: int, today: date, max_per_skill: int = 2
) -> tuple[list[DueReview], list[dict]]:
    """Due reviews of stages other than today's main goal. Nothing is deleted or postponed by a goal change: what
    does not fit is held with its reason and stays due. Several reviews on the same family / skill are merged."""
    picked: list[DueReview] = []
    held: list[dict] = []
    used = 0
    fam: set[str] = set()
    per_skill: dict[str, int] = {}
    order = sorted(due, key=lambda d: (0 if d.last_result == "incorrect" else 1, d.due_day, d.unit_id))
    for d in order:
        if d.due_day > today:
            continue
        if not d.available:
            held.append(
                {"unit_id": d.unit_id, "mode": d.mode, "reason_ja": d.unavailable_reason or "教材を使えないため保留"}
            )
        elif d.family in fam or per_skill.get(d.skill, 0) >= max_per_skill:
            held.append(
                {
                    "unit_id": d.unit_id,
                    "mode": d.mode,
                    "reason_ja": "同じ技能・素材の復習をまとめたため保留（削除しません）",
                }
            )
        elif used + d.est_sec > cap_sec:
            held.append(
                {"unit_id": d.unit_id, "mode": d.mode, "reason_ja": "今日の時間枠に入らないため保留（期限は残ります）"}
            )
        else:
            picked.append(d)
            used += d.est_sec
            fam.add(d.family)
            per_skill[d.skill] = per_skill.get(d.skill, 0) + 1
    return picked, held

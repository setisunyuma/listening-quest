"""Pure learning rules: learning day, first-exposure eligibility, band adjustment, spaced review, XP.

No DB access and no clocks: callers inject `today` / `now`. Thresholds come from learning_config.yaml.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


@dataclass(frozen=True)
class LearningConfig:
    review_intervals_days: tuple[int, ...] = (1, 3, 7, 14, 30)
    initial_band: int = 3
    band_min_units: int = 10
    band_window: int = 20
    band_up_threshold: float = 0.85
    band_down_threshold: float = 0.60
    band_max_change_per_day: int = 1
    skill_window: int = 20
    skill_min_observations: int = 5
    skill_weak_threshold: float = 0.70
    plan_split: dict = field(default_factory=lambda: {"review": 0.4, "weak": 0.4, "explore": 0.2})
    slot_cap_factor: float = 1.25
    answer_sec_per_question: int = 12
    feedback_sec_per_question: int = 25
    xp: dict = field(
        default_factory=lambda: {
            "unit_base": 10,
            "review_bonus": 5,
            "radio_unit": 5,
            "daily_plan": 20,
            "level_size": 100,
        }
    )
    radio_complete_ratio: float = 0.9
    same_session_recheck_min_gap_units: int = 3
    replenish: dict = field(default_factory=lambda: {"low_days": 1, "target_days": 3, "max_jobs_per_day": 12})
    difficulty_rubric: dict = field(default_factory=dict)


@lru_cache
def load_config() -> LearningConfig:
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "learning_config.yaml").read_text(encoding="utf-8"))
    raw["review_intervals_days"] = tuple(raw["review_intervals_days"])
    return LearningConfig(**raw)


# ---------------------------------------------------------------- learning day


def learning_day(now: datetime, tz: str = "Asia/Tokyo", cutoff_hour: int = 4) -> date:
    """The learning day flips at `cutoff_hour` local time. `now` must be timezone-aware (server clock)."""
    local = now.astimezone(ZoneInfo(tz))
    return (local - timedelta(hours=cutoff_hour)).date()


# ---------------------------------------------------------------- first-exposure eligibility

AID_KINDS = ("extra_replay", "speed", "transcript", "hint", "prompt_text")


@dataclass(frozen=True)
class AttemptFacts:
    session_type: str
    eligible_at_start: bool  # no exposure to the same script/audio/answers before this attempt started
    first_claim: bool  # this attempt holds the unique "first" claim for the stimulus
    plays: dict  # segment_id -> times played
    allowed_plays: dict  # segment_id -> official plays
    aids: dict  # aid kind -> bool
    external_answer_access: bool  # answers reached through another session after start (radio, other tab)
    complete: bool  # every target question answered, none skipped
    auto_graded: bool = True
    invalidated: bool = False


def initial_first_eligible(prior_exposure_kinds: Iterable[str]) -> bool:
    """Judged from history strictly *before* the attempt started. Any earlier contact with the same script,
    audio, answers or explanation disqualifies. Plays inside the attempt itself are not history."""
    return not any(True for _ in prior_exposure_kinds)


def judge_strict_first(f: AttemptFacts) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if f.session_type != "train":
        reasons.append(f"session_type:{f.session_type}")
    if not f.eligible_at_start:
        reasons.append("prior_exposure")
    if not f.first_claim:
        reasons.append("not_first_attempt")
    for seg, n in f.plays.items():
        if n > f.allowed_plays.get(seg, 1):
            reasons.append("extra_replay")
            break
    for k in AID_KINDS:
        if f.aids.get(k) and (k != "extra_replay" or "extra_replay" not in reasons):
            reasons.append(k if k != "speed" else "speed_changed")
    if f.external_answer_access:
        reasons.append("answer_access_elsewhere")
    if not f.complete:
        reasons.append("incomplete")
    if not f.auto_graded:
        reasons.append("not_auto_graded")
    if f.invalidated:
        reasons.append("invalidated")
    return (not reasons, reasons)


def is_assisted(plays: dict, allowed_plays: dict, aids: dict) -> bool:
    if any(aids.get(k) for k in AID_KINDS):
        return True
    return any(n > allowed_plays.get(seg, 1) for seg, n in plays.items())


# ---------------------------------------------------------------- difficulty band


@dataclass(frozen=True)
class BandDecision:
    band: int
    changed: bool
    reason: str
    n: int
    mean: float | None


def adjust_band(
    band: int,
    observations: list[tuple[str, float]],
    last_change_day: date | None,
    today: date,
    cfg: LearningConfig,
) -> BandDecision:
    """`observations`: (stimulus_fingerprint, unit_score) for strict-first, fully answered units obtained
    *since moving to the current band*, oldest first. One observation per distinct audio."""
    seen: set[str] = set()
    scores: list[float] = []
    for fp, score in observations:
        if fp in seen:
            continue
        seen.add(fp)
        scores.append(score)
    n = len(scores)
    if n < cfg.band_min_units:
        return BandDecision(band, False, f"need_more_units:{n}/{cfg.band_min_units}", n, None)
    window = scores[-cfg.band_window :]
    mean = sum(window) / len(window)
    if last_change_day is not None and last_change_day == today:
        return BandDecision(band, False, "max_one_step_per_day", n, mean)
    if mean >= cfg.band_up_threshold and band < 5:
        return BandDecision(band + 1, True, "mean_at_or_above_up_threshold", n, mean)
    if mean < cfg.band_down_threshold and band > 1:
        return BandDecision(band - 1, True, "mean_below_down_threshold", n, mean)
    return BandDecision(band, False, "hold", n, mean)


# ---------------------------------------------------------------- spaced review


@dataclass(frozen=True)
class Sched:
    stage: int
    due_day: date


@dataclass(frozen=True)
class SrsResult:
    sched: Sched
    changed: bool
    reason_code: str
    reason_ja: str


def srs_next(current: Sched | None, outcome: str, today: date, cfg: LearningConfig) -> SrsResult:
    """outcome: correct_unaided | correct_assisted | incorrect (includes partial/skip).

    Only a due review answered correctly without help advances the stage."""
    iv = cfg.review_intervals_days
    tomorrow = today + timedelta(days=iv[0])
    if current is None:
        return SrsResult(Sched(0, tomorrow), True, "first_learn", "初回学習の翌日に最初の復習")
    due = today >= current.due_day
    if outcome == "incorrect":
        return SrsResult(Sched(0, tomorrow), True, "incorrect_reset", "誤答のため翌日に再確認")
    if not due:
        return SrsResult(current, False, "early_repeat", "期限前の反復のため段階は据え置き")
    if outcome == "correct_assisted":
        return SrsResult(Sched(current.stage, tomorrow), True, "assisted_short", "補助を使ったため翌日に再確認")
    stage = min(current.stage + 1, len(iv) - 1)
    return SrsResult(
        Sched(stage, today + timedelta(days=iv[stage])), True, "advance", f"自力正解のため{iv[stage]}日後に復習"
    )


# ---------------------------------------------------------------- XP / radio coverage


def level_for(total_xp: int, cfg: LearningConfig) -> int:
    return 1 + math.floor(total_xp / cfg.xp["level_size"])


def merge_ranges(ranges: Iterable[Iterable[int]]) -> list[list[int]]:
    rs = sorted([int(a), int(b)] for a, b in ranges if b > a)
    out: list[list[int]] = []
    for a, b in rs:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def coverage_ratio(ranges: list[list[int]], start: int, end: int) -> float:
    """Share of [start, end) covered by the union of actually played ranges (seeking does not count)."""
    if end <= start:
        return 0.0
    covered = sum(max(0, min(b, end) - max(a, start)) for a, b in merge_ranges(ranges))
    return covered / (end - start)


# ---------------------------------------------------------------- skill status


def skill_status(n: int, mean: float | None, cfg: LearningConfig) -> str:
    if n == 0:
        return "unmeasured"  # 未測定
    if n < cfg.skill_min_observations:
        return "reference"  # 参考値・判定中
    assert mean is not None
    if mean < cfg.skill_weak_threshold:
        return "weak"
    if mean >= cfg.band_up_threshold:
        return "strong"
    return "developing"

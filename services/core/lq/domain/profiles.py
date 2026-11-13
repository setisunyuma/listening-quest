"""Versioned mode profiles (format, presentation, official plays, scoring). Files live in lq/profiles/*.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from lq.domain.content import canonical_json, sha256_hex

PROFILE_DIR = Path(__file__).resolve().parents[1] / "profiles"


@lru_cache
def load_profiles() -> dict[str, dict]:
    out = {}
    for p in sorted(PROFILE_DIR.glob("*.json")):
        prof = json.loads(p.read_text(encoding="utf-8"))
        validate_profile(prof)
        out[prof["id"]] = prof
    return out


def profile_hash(profile: dict) -> str:
    return sha256_hex(canonical_json(profile))


def get_profile(profile_id: str) -> dict:
    return load_profiles()[profile_id]


def current_profile(mode: str) -> dict:
    for prof in load_profiles().values():
        if prof["mode"] == mode and prof.get("current"):
            return prof
    raise KeyError(mode)


def part_spec(profile: dict, part: str) -> dict | None:
    for p in profile["parts"]:
        if p["part"] == part:
            return p
    return None


def slot_layout(profile: dict) -> list[dict]:
    """Global answer-slot numbers per (part, unit index, question index), in exam order."""
    out, slot = [], 1
    for p in profile["parts"]:
        for ui in range(p["units"]):
            slots = list(range(slot, slot + p["questions_per_unit"]))
            slot += p["questions_per_unit"]
            out.append({"part": p["part"], "section": p["section"], "unit_index": ui, "slots": slots})
    return out


def total_slots(profile: dict) -> int:
    return sum(p["units"] * p["questions_per_unit"] for p in profile["parts"])


def validate_profile(profile: dict) -> None:
    n = total_slots(profile)
    if profile["exam"]["score"] == "points":
        seen: list[int] = []
        total = 0.0
        for rule in profile["scoring"]:
            seen += rule["slots"]
            total += rule["points"] if rule.get("all_or_nothing") else rule["points_each"] * len(rule["slots"])
        if sorted(seen) != list(range(1, n + 1)):
            raise ValueError(f"{profile['id']}: scoring rules must cover slots 1..{n} exactly once")
        if abs(total - profile["exam"]["total_points"]) > 1e-9:
            raise ValueError(f"{profile['id']}: scoring rules sum to {total}")
        sec_total = sum(profile["section_points"].values())
        if abs(sec_total - total) > 1e-9:
            raise ValueError(f"{profile['id']}: section points sum to {sec_total}")
    else:
        if profile["exam"]["total_questions"] != n:
            raise ValueError(f"{profile['id']}: total_questions != {n}")


def score_exam(profile: dict, slot_correct: dict[int, bool]) -> dict:
    """Score a full set. Unanswered slots must be passed as False (they score 0).

    Per-slot correctness (learning signal) is kept separate from points (exam scoring groups)."""
    layout = slot_layout(profile)
    n = total_slots(profile)
    sec_of = {s: item["section"] for item in layout for s in item["slots"]}
    by_section: dict[str, dict] = {}
    for s in range(1, n + 1):
        sec = by_section.setdefault(sec_of[s], {"correct": 0, "total": 0, "points": 0.0, "max_points": 0.0})
        sec["total"] += 1
        sec["correct"] += 1 if slot_correct.get(s) else 0
    out = {
        "score_kind": profile["exam"]["score"],
        "correct_count": sum(1 for s in range(1, n + 1) if slot_correct.get(s)),
        "slot_count": n,
        "by_section": by_section,
    }
    if profile["exam"]["score"] == "points":
        total = 0.0
        groups = []
        for rule in profile["scoring"]:
            if rule.get("all_or_nothing"):
                ok = all(slot_correct.get(s) for s in rule["slots"])
                pts = rule["points"] if ok else 0.0
                mx = rule["points"]
            else:
                pts = sum(rule["points_each"] for s in rule["slots"] if slot_correct.get(s))
                mx = rule["points_each"] * len(rule["slots"])
            total += pts
            sec = by_section[sec_of[rule["slots"][0]]]
            sec["points"] += pts
            sec["max_points"] += mx
            groups.append({"slots": rule["slots"], "points": pts, "max_points": mx, "all_or_nothing": bool(rule.get("all_or_nothing"))})
        out["points"] = total
        out["max_points"] = profile["exam"]["total_points"]
        out["groups"] = groups
    return out

"""Export / restore of the learner's history and settings as JSON. Restore validates first, inserts only
rows that do not exist yet (ids are preserved) and never overwrites - repeating it changes nothing."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lq.db import models as m
from lq.domain.content import MODES, canonical_json, sha256_hex

EXPORT_VERSION = 1
TABLES = [  # (key, model, parent-first order)
    ("mode_states", m.ModeState), ("learning_sessions", m.LearningSession), ("daily_plans", m.DailyPlan), ("plan_slots", m.PlanSlot), ("attempts", m.Attempt),
    ("exposure_events", m.ExposureEvent), ("skill_observations", m.SkillObservation), ("review_schedules", m.ReviewSchedule), ("self_reports", m.SelfReport), ("xp_events", m.XpEvent),
]  # fmt: skip
UNIT_FK = {"attempts", "exposure_events", "skill_observations", "review_schedules", "self_reports", "plan_slots"}


def _ser(v):
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, date):
        return {"$d": v.isoformat()}
    return v


def _de(v):
    if isinstance(v, dict) and len(v) == 1:
        if "$dt" in v:
            return datetime.fromisoformat(v["$dt"])
        if "$d" in v:
            return date.fromisoformat(v["$d"])
    return v


def _rows(session, model, user: m.User) -> list[dict]:
    if model is m.PlanSlot:
        q = select(model).join(m.DailyPlan, m.DailyPlan.id == model.plan_id).where(m.DailyPlan.user_id == user.id)
    else:
        q = select(model).where(model.user_id == user.id)
    return [{c.name: _ser(getattr(r, c.name)) for c in model.__table__.columns} for r in session.execute(q).scalars()]


def export_user(session, user: m.User) -> dict:
    data = {key: _rows(session, model, user) for key, model in TABLES}
    unit_ids = {r["unit_id"] for key in UNIT_FK for r in data[key] if r.get("unit_id")}
    units = session.execute(select(m.Unit).where(m.Unit.id.in_(unit_ids))).scalars().all() if unit_ids else []
    body = {
        "export_version": EXPORT_VERSION, "exported_at": datetime.now(UTC).isoformat(), "realm": user.realm, "username": user.username, "settings": user.settings or {},
        "unit_refs": [{"id": u.id, "source": u.source, "content_id": u.content_id, "revision": u.revision, "content_hash": u.content_hash} for u in units],
        "data": data,
    }  # fmt: skip
    body["checksum"] = sha256_hex(canonical_json({k: v for k, v in body.items() if k != "exported_at"}))
    return body


def validate_export(body: dict) -> list[str]:
    errs = []
    if not isinstance(body, dict) or body.get("export_version") != EXPORT_VERSION:
        return ["unsupported or missing export_version"]
    if body.get("checksum") != sha256_hex(canonical_json({k: v for k, v in body.items() if k not in ("exported_at", "checksum")})):
        errs.append("checksum mismatch: the file was modified or is damaged")
    data = body.get("data")
    if not isinstance(data, dict):
        return [*errs, "data section missing"]
    for key, model in TABLES:
        rows = data.get(key)
        if not isinstance(rows, list):
            errs.append(f"{key}: missing")
            continue
        cols = {c.name for c in model.__table__.columns}
        for r in rows[:5000]:
            if not isinstance(r, dict) or set(r) != cols:
                errs.append(f"{key}: row shape does not match this version")
                break
    for a in (data.get("attempts") or [])[:5000]:
        if isinstance(a, dict) and a.get("mode") not in MODES:
            errs.append("attempts: unknown mode")
            break
    return errs


def restore_user(session, user: m.User, body: dict, *, apply_settings: bool = False) -> dict:
    errs = validate_export(body)
    if errs:
        return {"ok": False, "errors": errs}
    if body.get("realm") != user.realm:
        return {"ok": False, "errors": [f"this export belongs to the {body.get('realm')} data area, not {user.realm}"]}
    # map exported unit ids to units present here (same source/content_id/revision AND same content)
    unit_map: dict[str, str] = {}
    for ref in body["unit_refs"]:
        u = session.execute(select(m.Unit).where(m.Unit.realm == user.realm, m.Unit.source == ref["source"], m.Unit.content_id == ref["content_id"], m.Unit.revision == ref["revision"])).scalar_one_or_none()
        if u is not None and u.content_hash == ref["content_hash"]:
            unit_map[ref["id"]] = u.id
    report = {"ok": True, "inserted": {}, "skipped_existing": {}, "skipped_missing_unit": {}, "settings_applied": False}
    kept_plans: set[str] = set()
    for key, model in TABLES:
        ins = skip = miss = 0
        for raw in body["data"][key]:
            row = {k: _de(v) for k, v in raw.items()}
            if "user_id" in row:
                row["user_id"] = user.id
            if key in UNIT_FK and row.get("unit_id"):
                if row["unit_id"] not in unit_map:
                    miss += 1
                    continue
                row["unit_id"] = unit_map[row["unit_id"]]
            if key == "plan_slots" and row["plan_id"] not in kept_plans:
                skip += 1
                continue
            with session.begin_nested():
                res = session.execute(pg_insert(model).values(**row).on_conflict_do_nothing())
            if res.rowcount == 1:
                ins += 1
            else:
                skip += 1  # already here (same id or same natural key): the existing row wins
            if key == "daily_plans" and session.get(m.DailyPlan, row["id"]) is not None:
                kept_plans.add(row["id"])
        report["inserted"][key], report["skipped_existing"][key], report["skipped_missing_unit"][key] = ins, skip, miss
    if apply_settings:
        user.settings = body.get("settings") or {}
        report["settings_applied"] = True
    session.flush()
    from lq.services import stats

    report["recomputed"] = [stats.recompute_mode_state(session, user, mode) for mode in MODES]
    return report

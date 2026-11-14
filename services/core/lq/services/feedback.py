"""Learner feedback on material: reports about a unit, and lines the learner could not catch.

Reports: "wrong" (answer/explanation is wrong) and "audio" (voice or audio is broken) withdraw the unit at once
- the same as the owner's invalidation, so history stays and statistics are recomputed - and the owner can put
it back from 教材管理. "unclear" and "other" are only recorded for the owner to look at."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select

from lq.db import models as m
from lq.services import admin, stats
from lq.services import learning as L
from lq.services.content import load_content

KINDS = {
    "unclear": "意味・解説が分かりにくい",
    "wrong": "答え・解説がおかしい",
    "audio": "音声がおかしい（声・読み間違い・雑音）",
    "other": "その他",
}
WITHDRAW = {"wrong", "audio"}


def report(session, user: m.User, unit_id: str, kind: str, note: str = "", attempt_id: str | None = None,
           now: datetime | None = None) -> dict:  # fmt: skip
    if kind not in KINDS:
        raise L.Invalid("unknown report kind")
    now = now or datetime.now(UTC)
    unit = L.get_unit_for(session, user, unit_id)
    rep = m.UnitReport(user_id=user.id, unit_id=unit.id, attempt_id=attempt_id, kind=kind, note=(note or "")[:1000],
                       status="open", created_at=now)  # fmt: skip
    session.add(rep)
    session.flush()
    withdrawn = False
    if kind in WITHDRAW and unit.invalidated_at is None:
        admin.invalidate_unit(
            session, user, unit.id, f"報告により停止: {KINDS[kind]}" + (f"（{note[:200]}）" if note else ""), now
        )
        withdrawn = True
    return {
        "report_id": rep.id,
        "kind": kind,
        "withdrawn": withdrawn,
        "unit_withdrawn": unit.invalidated_at is not None,
    }


def list_reports(session, realm: str, include_resolved: bool = False) -> list[dict]:
    R, U = m.UnitReport, m.Unit
    q = select(R, U).join(U, U.id == R.unit_id).where(U.realm == realm).order_by(R.created_at.desc()).limit(200)
    if not include_resolved:
        q = q.where(R.status == "open")
    out = []
    for r, u in session.execute(q).all():
        out.append({
            "id": r.id, "unit_id": u.id, "title": u.title, "mode": u.mode, "part": u.part, "kind": r.kind,
            "kind_label": KINDS.get(r.kind, r.kind), "note": r.note, "status": r.status, "resolution": r.resolution,
            "created_at": r.created_at.isoformat(), "unit_withdrawn": u.invalidated_at is not None,
        })  # fmt: skip
    return out


def resolve(session, user: m.User, report_id: str, action: str, now: datetime | None = None) -> dict:
    """action: dismiss (keep the unit as it is) | withdraw (invalidate) | restore (put a withdrawn unit back)."""
    now = now or datetime.now(UTC)
    rep = session.get(m.UnitReport, report_id)
    if rep is None:
        raise L.NotFound("report not found")
    unit = L.get_unit_for(session, user, rep.unit_id)
    if action == "withdraw":
        if unit.invalidated_at is None:
            admin.invalidate_unit(session, user, unit.id, f"報告を確認して停止: {KINDS.get(rep.kind, rep.kind)}", now)
    elif action == "restore":
        restore_unit(session, user, unit.id)
    elif action != "dismiss":
        raise L.Invalid("unknown action")
    # one decision settles every open report on the same unit
    for r in session.execute(
        select(m.UnitReport).where(m.UnitReport.unit_id == unit.id, m.UnitReport.status == "open")
    ).scalars():
        r.status, r.resolution, r.resolved_at = "resolved", action, now
    return {"unit_id": unit.id, "action": action, "unit_withdrawn": unit.invalidated_at is not None}


def restore_unit(session, user: m.User, unit_id: str) -> dict:
    unit = L.get_unit_for(session, user, unit_id)
    if unit.invalidated_at is not None:
        unit.invalidated_at, unit.invalidated_reason = None, None
        session.add(m.AuditLog(realm=unit.realm, actor=user.username, action="unit_restored",
                               target=f"{unit.source}/{unit.content_id}@{unit.revision}", details={"unit_id": unit.id}))  # fmt: skip
        session.flush()
        for u in session.execute(select(m.User).where(m.User.realm == unit.realm)).scalars():
            stats.recompute_mode_state(session, u, unit.mode)
    return {"unit_id": unit.id, "restored": True}


# ---------------------------------------------------------------- missed lines


def set_missed(session, user: m.User, unit_id: str, utterance_id: str, missed: bool) -> list[str]:
    unit = L.get_unit_for(session, user, unit_id)
    if utterance_id not in load_content(unit).utterance_map():
        raise L.Invalid("unknown line")
    M = m.MissedLine
    if missed:
        if session.get(M, (user.id, unit.id, utterance_id)) is None:
            session.add(M(user_id=user.id, unit_id=unit.id, utterance_id=utterance_id))
    else:
        session.execute(delete(M).where(M.user_id == user.id, M.unit_id == unit.id, M.utterance_id == utterance_id))
    session.flush()
    return missed_ids(session, user, unit.id)


def missed_ids(session, user: m.User, unit_id: str) -> list[str]:
    M = m.MissedLine
    return list(session.execute(select(M.utterance_id).where(M.user_id == user.id, M.unit_id == unit_id)).scalars())


def units_with_missed(session, user: m.User) -> set[str]:
    return set(session.execute(select(m.MissedLine.unit_id).where(m.MissedLine.user_id == user.id)).scalars())


def missed_list(session, user: m.User, mode: str | None = None) -> list[dict]:
    """Every line the learner marked, newest first, with its audio so it can be replayed on its own."""
    M, U = m.MissedLine, m.Unit
    q = (
        select(M, U)
        .join(U, U.id == M.unit_id)
        .where(M.user_id == user.id, U.realm == user.realm, U.invalidated_at.is_(None))
        .order_by(M.created_at.desc())
    )
    if mode:
        q = q.where(U.mode == mode)
    out, cache = [], {}
    for ml, u in session.execute(q).all():
        if u.id not in cache:
            content = load_content(u)
            cache[u.id] = (content.utterance_map(), {s.id: (s.label or s.id) for s in content.speakers},
                           L.unit_assets(session, u.id))  # fmt: skip
        utt, labels, assets = cache[u.id]
        line = utt.get(ml.utterance_id)
        if line is None:
            continue
        a = assets.get(f"utt:{line.id}")
        out.append({
            "unit_id": u.id, "title": u.title, "mode": u.mode, "utterance_id": line.id,
            "speaker": labels.get(line.speaker_id, line.speaker_id), "text": line.text,
            "audio_url": L.audio_url(a) if a else None, "marked_at": ml.created_at.isoformat(),
        })  # fmt: skip
    return out


# ---------------------------------------------------------------- dictation


def check_dictation(session, user: m.User, unit_id: str, utterance_id: str, typed: str) -> dict:
    """Compare what the learner typed with the line, word by word, after the same normalisation the audio
    check uses (numbers, contractions, punctuation and case do not count as mistakes). Free: no API call."""
    unit = L.get_unit_for(session, user, unit_id)
    answered = session.execute(
        select(m.Attempt.id).where(
            m.Attempt.user_id == user.id, m.Attempt.unit_id == unit.id, m.Attempt.status == "submitted"
        )
    ).first()
    if not answered:
        raise L.Forbidden("dictation shows the script: answer this unit first")
    line = load_content(unit).utterance_map().get(utterance_id)
    if line is None:
        raise L.Invalid("unknown line")
    from lq.audio.align import compare

    cmp = compare(line.text, typed or "")
    ref, hyp = cmp["ref"], cmp["hyp"]
    # partial score: 1 - word edits / words in the line (insertions count, so an added "not" costs points)
    score = max(0.0, 1.0 - cmp["edits"] / len(ref)) if ref else 0.0
    ops = []
    for op, i, j in cmp["ops"]:
        ops.append(
            {"op": op, "expected": [ref[i]] if i is not None else [], "typed": [hyp[j]] if j is not None else []}
        )
    reasons = {"key_changed": "意味を変える語が違います", "key_missing": "意味を変える語が抜けています",
               "key_added": "意味を変える語が余分です", "name_swapped": "人物（名前）が入れ替わっています"}  # fmt: skip
    critical = [{**f, "reason_ja": reasons[f["type"]]} for f in cmp["findings"]]
    return {
        "exact": bool(ref) and cmp["edits"] == 0,
        "score": round(score, 3),
        "edits": cmp["edits"],
        "correct_words": sum(1 for op, _, _ in cmp["ops"] if op == "equal"),
        "total_words": len(ref),
        "critical": critical,
        "ops": ops,
        "text": line.text,
        "empty": not hyp,
    }

"""Practice tasks in the learner's day (統合実装指示書 G01 / U02 / M01).

- "Unlocked" means a task kind may be used in the daily plan. It never certifies a grade or CEFR level, and it is
  never taken back after wrong answers, a pause or a changed exam date.
- The stage's own entry tasks are available once that stage is the main goal; other kinds open by evidence
  (domain.progression.practice_unlock) or by the learner's own wish (manual start: no evidence is granted).
- Missing material, a paused budget or pending review are shown apart from "not yet".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from lq.db import models as m
from lq.domain import practice as PR
from lq.domain import progression as P
from lq.services import learning as L
from lq.services.content import load_content

STATUS_JA = {
    "unlocked": "日課に解放済み", "consolidating": "定着を確認中", "next": "次に挑戦できること",
    "preview": "先行体験", "locked": "まだ（前提の証拠を集めています）",
}  # fmt: skip


def _states(session, user: m.User) -> dict[str, m.PracticeState]:
    v = PR.load_curriculum()["version"]
    rows = session.execute(
        select(m.PracticeState).where(m.PracticeState.user_id == user.id, m.PracticeState.curriculum_version == v)
    ).scalars()
    return {r.practice_kind: r for r in rows}


def _practice_attempts(session, user: m.User) -> list[tuple[m.Attempt, m.Unit]]:
    A, U = m.Attempt, m.Unit
    return list(
        session.execute(
            select(A, U).join(U, U.id == A.unit_id).where(
                A.user_id == user.id, A.status == "submitted", U.template_type == "practice_task")
            .order_by(A.submitted_at)
        ).all()
    )  # fmt: skip


def _evidence(session, user: m.User, kinds: set[str], skills: set[str]) -> list[P.TaskEvidence]:
    """Prerequisite evidence: earlier practice kinds (their own rubric scale) and, for entry tasks, unaided
    first-exposure exam items on the prerequisite skills. Self reports, radio and explanations never count."""
    out: list[P.TaskEvidence] = []
    for att, unit in _practice_attempts(session, user):
        res = (att.result or {}).get("practice") or {}
        if res.get("practice_kind") not in kinds or res.get("first_understanding") is None:
            continue
        content = load_content(unit)
        out.append(P.TaskEvidence(day=att.learning_day, kind=res["practice_kind"], family=unit.family_id, scene=content.practice.scene,
                                  score=float(res["first_understanding"]), eligible=bool(res.get("evidence_eligible")),
                                  critical=bool(res.get("critical_first"))))  # fmt: skip
    if skills:
        SO, U = m.SkillObservation, m.Unit
        for so, fam, part in session.execute(
            select(SO, U.family_id, U.part).join(U, U.id == SO.unit_id).where(
                SO.user_id == user.id, SO.kind == "strict_first", SO.skill.in_(skills), U.invalidated_at.is_(None))
        ).all():  # fmt: skip
            out.append(
                P.TaskEvidence(
                    day=so.created_at.date(), kind=f"exam:{so.skill}", family=fam, scene=part, score=so.score
                )
            )
    return out


def _ready_units(session, user: m.User, kind: str) -> list[m.Unit]:
    U = m.Unit
    return list(
        session.execute(
            select(U).where(U.realm == user.realm, U.template_type == "practice_task", U.part == kind, U.status == "READY",
                            U.invalidated_at.is_(None), U.superseded_by.is_(None)).order_by(U.id)
        ).scalars()
    )  # fmt: skip


def unseen_unit(session, user: m.User, kind: str) -> m.Unit | None:
    """A READY task of this kind whose audio and family the learner has not met (no contact of any kind)."""
    E = m.ExposureEvent
    fps = set(session.execute(select(E.stimulus_fingerprint).where(E.user_id == user.id)).scalars())
    fams = set(session.execute(select(E.family_id).where(E.user_id == user.id)).scalars())
    for u in _ready_units(session, user, kind):
        if u.stimulus_fingerprint not in fps and u.family_id not in fams:
            return u
    return None


def unseen_count(session, user: m.User, kind: str) -> int:
    E = m.ExposureEvent
    fps = set(session.execute(select(E.stimulus_fingerprint).where(E.user_id == user.id)).scalars())
    fams = set(session.execute(select(E.family_id).where(E.user_id == user.id)).scalars())
    return sum(
        1 for u in _ready_units(session, user, kind) if u.stimulus_fingerprint not in fps and u.family_id not in fams
    )


def judge(session, user: m.User, kind: str) -> dict:
    t = PR.task_def(kind)
    prereq = set(t.get("prerequisites", [])) | set(t.get("prereq_from_kinds", []))
    skills = set() if prereq else set(t.get("prereq_skills", []))
    ev = _evidence(session, user, prereq, skills)
    if not prereq:  # entry task: per-skill means of exam evidence, each on its own scale
        ev = [e for e in ev if e.kind.startswith("exam:")]
    return P.practice_unlock(kind, (prereq or {f"exam:{s}" for s in skills}), ev)


def _state(session, user: m.User, kind: str, now: datetime) -> m.PracticeState:
    """The learner's record for a task kind, created when first needed (status 'preview' = not opened yet)."""
    st = _states(session, user).get(kind)
    if st is None:
        st = m.PracticeState(user_id=user.id, practice_kind=kind, curriculum_version=PR.load_curriculum()["version"], status="preview",
                             evidence=[], rule_version=P.load_progression()["version"], manual_started=False, recommendation={}, updated_at=now)  # fmt: skip
        session.add(st)
        session.flush()
    return st


def _unlock(session, user: m.User, kind: str, reason: str, rec: dict, now: datetime) -> m.PracticeState:
    st = _states(session, user).get(kind)
    v = PR.load_curriculum()["version"]
    if st is None:
        st = m.PracticeState(user_id=user.id, practice_kind=kind, curriculum_version=v, status="unlocked", unlocked_at=now,
                             unlock_reason=reason, evidence=[], rule_version=P.load_progression()["version"], manual_started=reason == "manual",
                             recommendation=rec, updated_at=now)  # fmt: skip
        session.add(st)
    elif st.status != "unlocked":
        st.status, st.unlocked_at, st.unlock_reason, st.recommendation, st.updated_at = (
            "unlocked",
            now,
            reason,
            rec,
            now,
        )
        st.manual_started = st.manual_started or reason == "manual"
    session.flush()
    return st


def after_submit(session, user: m.User, att: m.Attempt, now: datetime) -> list[str]:
    """Keep three records apart: (1) the kind was tried before it was open to this learner, (2) the unlock criteria of
    the kinds that build on it are met - recorded once with the evidence and rule version, even if the kind was already
    open by the main goal or a wish - and (3) the kind was opened (by those criteria). Nothing is ever locked again."""
    kind = ((att.result or {}).get("practice") or {}).get("practice_kind")
    opened = []
    if kind and not is_open(session, user, kind):
        st = _state(session, user, kind, now)
        st.tried_at = st.tried_at or now  # tried first (a preview): no evidence of mastery is granted
    for k, t in PR.load_curriculum()["tasks"].items():
        if kind not in set(t.get("prerequisites", [])) | set(t.get("prereq_from_kinds", [])):
            continue
        st = _states(session, user).get(k)
        if st is not None and st.criteria_met_at is not None:
            continue
        res = judge(session, user, k)
        if res["status"] == "recommend":
            st = _state(session, user, k, now)
            st.criteria_met_at, st.criteria = now, {**res, "attempt_id": att.id}
            if st.status != "unlocked":
                st = _unlock(session, user, k, "recommended", res, now)
                st.evidence = [att.id]
                opened.append(k)
    return opened


def manual_start(session, user: m.User, kind: str, now: datetime) -> dict:
    """The learner's own wish: usable from today even with no history (a supported introduction). No evidence,
    mastery or XP is granted by this."""
    if kind not in PR.load_curriculum()["tasks"]:
        raise L.Invalid("unknown practice task")
    _unlock(session, user, kind, "manual", {"status": "manual"}, now)
    from lq.services import plans

    added = plans.add_practice_today(session, user, kind, now)
    return {"kind": kind, "added_today": added, **overview(session, user, now)}


def _stage_open(user: m.User, kind: str) -> bool:
    """Every practice task of the learner's chosen main goal is part of its daily work (the choice is the learner's;
    the unlock criteria are recorded apart)."""
    return PR.task_def(kind)["stage"] == L.user_settings(user)["active_mode"]


def is_open(session, user: m.User, kind: str) -> bool:
    st = _states(session, user).get(kind)
    return (st is not None and st.status == "unlocked") or _stage_open(user, kind)


def note_goal_opened(session, user: m.User, now: datetime) -> None:
    """Record when the main goal opened its stage's tasks (a separate record from criteria and wishes)."""
    for k in PR.load_curriculum()["tasks"]:
        if _stage_open(user, k):
            st = _state(session, user, k, now)
            st.goal_opened_at = st.goal_opened_at or now


def recent_counts(session, user: m.User, last: int = 20) -> dict[str, int]:
    """How often each kind was practised among the learner's latest practice tasks (no wall clock involved)."""
    out: dict[str, int] = {}
    for att, _u in _practice_attempts(session, user)[-last:]:
        k = ((att.result or {}).get("practice") or {}).get("practice_kind")
        out[k] = out.get(k, 0) + 1
    return out


def available_kinds(session, user: m.User) -> list[str]:
    """Open kinds in the order the day should offer them. The main goal's own kinds lead, least practised first, so no
    kind starves; a kind opened outside the main goal (by criteria or a wish) is brought in when neither of the last two
    practice tasks was such a kind - about one day in three - so it is introduced without displacing the goal's work."""
    cur = L.user_settings(user)["active_mode"]
    open_ = [k for k in PR.load_curriculum()["tasks"] if is_open(session, user, k)]
    n = recent_counts(session, user)
    goal = sorted((k for k in open_ if PR.task_def(k)["stage"] == cur), key=lambda k: (n.get(k, 0), k))
    other = sorted((k for k in open_ if PR.task_def(k)["stage"] != cur), key=lambda k: (n.get(k, 0), k))
    last2 = [
        ((a.result or {}).get("practice") or {}).get("practice_kind")
        for a, _u in _practice_attempts(session, user)[-2:]
    ]
    if other and goal and set(last2) & set(other):
        return goal + other
    return other + goal if other else goal


def material_status(session, user: m.User, kind: str) -> dict:
    """Why a task has no unseen material right now - shown as 'waiting for material', never as a lack of ability."""
    if unseen_unit(session, user, kind) is not None:
        return {"state": "ready", "label_ja": None}
    from lq.generator import budget

    J = m.GenerationJob
    jobs = session.execute(
        select(J).where(J.realm == user.realm, J.status.in_(("QUEUED", "RUNNING", "RETRY_WAIT", "BLOCKED")))
    ).scalars().all()  # fmt: skip
    mine = [j for j in jobs if (j.params or {}).get("practice_kind") == kind]
    cfg = budget.get_system(session, user.realm)
    if any(j.status == "BLOCKED" and j.blocked_reason in ("budget", "monthly_budget") for j in mine):
        return {
            "state": "budget",
            "label_ja": "教材待ち: 予算の上限に達したため生成を止めています（日本時間の午前4時以降に再開）",
        }
    if mine:
        return {"state": "preparing", "label_ja": "教材待ち: いま生成しています"}
    if user.realm != "demo" and not (cfg.get("paid_enabled") and cfg.get("auto_replenish")):
        return {"state": "off", "label_ja": "教材待ち: 有料生成または自動補充がオフのため、新しい教材が作られません"}
    return {"state": "waiting", "label_ja": "教材待ち: 次の自動補充で用意します（能力不足ではありません）"}


def can_do(session, user: m.User) -> dict[str, list[dict]]:
    """M01: what the learner has done, as statements with counts and the latest date (first understanding only,
    eligible evidence only). No statement is made from assisted, pending or ineligible attempts."""
    out: dict[str, dict[str, dict]] = {}
    for att, _unit in _practice_attempts(session, user):
        res = (att.result or {}).get("practice") or {}
        if not res.get("evidence_eligible"):
            continue
        labels = {r["id"]: r["label_ja"] for r in PR.task_def(res["practice_kind"])["rubric"]}
        for item in res["items"].values():
            if item["first"]["correct"]:
                d = out.setdefault(res["practice_kind"], {}).setdefault(
                    item["rubric"], {"label_ja": labels.get(item["rubric"], item["rubric"]), "n": 0, "last": None}
                )
                d["n"] += 1
                d["last"] = att.learning_day.isoformat()
    return {k: list(v.values()) for k, v in out.items()}


def overview(session, user: m.User, now: datetime) -> dict:
    cur = PR.load_curriculum()
    st = _states(session, user)
    attempts = _practice_attempts(session, user)
    done = can_do(session, user)
    rows = []
    for k, t in cur["tasks"].items():
        s = st.get(k)
        mine = [(a, u) for a, u in attempts if ((a.result or {}).get("practice") or {}).get("practice_kind") == k]
        if is_open(session, user, k):
            later = {
                a.learning_day for a, _ in mine if ((a.result or {}).get("practice") or {}).get("evidence_eligible")
            }
            status = "consolidating" if len(later) >= 2 else "unlocked"
            missing = []
        else:
            res = judge(session, user, k)
            status = "next" if res["status"] == "recommend" else ("preview" if mine else "locked")
            missing = res["missing"]
        mat = material_status(session, user, k)
        rows.append({
            "kind": k, "stage": t["stage"], "title_ja": t["title_ja"], "goal_ja": t["goal_ja"], "scene_ja": t["scene_ja"],
            "minutes": t["minutes"], "status": status, "status_ja": STATUS_JA[status],
            "reason_ja": ("現在の主目標の練習" if _stage_open(user, k) else "本人の希望で開始" if s and s.unlock_reason == "manual"
                          else "前提の証拠がそろった" if s and s.unlock_reason == "recommended" else None),
            "missing": missing, "prerequisites": t.get("prerequisites", []),
            # material shortage is not a lack of ability
            "material_ja": mat["label_ja"], "material_state": mat["state"],
            # three separate records: tried first / unlock criteria met / opened by the chosen main goal
            "records": {"tried_at": s.tried_at.isoformat() if s and s.tried_at else None,
                        "criteria_met_at": s.criteria_met_at.isoformat() if s and s.criteria_met_at else None,
                        "goal_opened_at": s.goal_opened_at.isoformat() if s and s.goal_opened_at else None,
                        "manual": bool(s and s.manual_started)},
            "attempts": len(mine), "can_do": done.get(k, []),
        })  # fmt: skip
    return {
        "version": cur["version"],
        "tasks": rows,
        "note_ja": "実践課題の解放は「日課で使えるようになった」という意味で、級や CEFR の認定ではありません。",
    }

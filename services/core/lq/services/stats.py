"""Read models: home, library, growth statistics, history. Five kinds of numbers stay separate:
volume, first-attempt results, delayed-review retention, assisted results, self-reports / radio contact."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from lq.db import models as m
from lq.domain import rules
from lq.domain.content import MODE_LABELS_JA, MODES, SKILL_LABELS_JA, SKILLS
from lq.domain.profiles import current_profile
from lq.services import learning as L
from lq.services import plans

STATUS_LABELS = {
    "new": "未学習",
    "learned": "学習済み",
    "review_due": "復習予定（期限到来）",
    "review_scheduled": "復習予定",
    "perfect_first": "初見全問正解",
    "perfect_review": "復習全問正解",
    "radio_heard": "ラジオで聴取済み",
}


def _valid_attempts(session, user: m.User, mode: str | None = None, since: datetime | None = None):
    A, U = m.Attempt, m.Unit
    q = select(A, U).join(U, U.id == A.unit_id).where(A.user_id == user.id, A.status == "submitted")
    if mode:
        q = q.where(A.mode == mode)
    if since:
        q = q.where(A.submitted_at >= since)
    return session.execute(q.order_by(A.submitted_at)).all()


def streak_days(session, user: m.User, today: date) -> int:
    days = set(session.execute(select(m.XpEvent.learning_day).where(m.XpEvent.user_id == user.id).distinct()).scalars())
    days |= set(
        session.execute(
            select(m.Attempt.learning_day)
            .where(m.Attempt.user_id == user.id, m.Attempt.status == "submitted")
            .distinct()
        ).scalars()
    )
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def home(session, user: m.User, now: datetime) -> dict:
    cfg = rules.load_config()
    s = L.user_settings(user)
    today = L.user_day(user, now)
    plan = plans.get_plan(session, user, today)
    carried = plans.carried_plan(session, user, today)
    xp = L.total_xp(session, user.id)
    recent = []
    for a, u in reversed(_valid_attempts(session, user, since=now - timedelta(days=14))[-40:]):
        if (
            u.invalidated_at is None
            and a.complete
            and a.result["correct_count"] == a.result["total"]
            and (a.category == "first" or L.is_unassisted_review(a))
        ):
            recent.append(
                {
                    "kind": "perfect_first" if a.category == "first" else "perfect_review",
                    "title": u.title,
                    "mode": u.mode,
                    "at": a.submitted_at.isoformat(),
                }
            )
        if len(recent) >= 5:
            break
    modes = []
    for mode in MODES:
        st = L.get_mode_state(session, user.id, mode)
        ready = len(plans.candidate_units(session, user, mode))
        modes.append({"mode": mode, "label": MODE_LABELS_JA[mode], "band": st.difficulty_band, "ready_units": ready})
    from lq.services import assessment

    checks = [
        assessment.mock_set_dto(ms)
        for ms in session.execute(
            select(m.MockSet)
            .where(m.MockSet.realm == user.realm, m.MockSet.status == "READY", m.MockSet.mode == s["active_mode"])
            .order_by(m.MockSet.created_at)
        ).scalars()
    ]
    day_xp = int(
        session.execute(
            select(func.coalesce(func.sum(m.XpEvent.amount), 0)).where(
                m.XpEvent.user_id == user.id, m.XpEvent.learning_day == today
            )
        ).scalar_one()
    )
    return {
        "today": today.isoformat(), "settings": s, "realm": user.realm, "username": user.username,
        "plan": plans.plan_dto(session, plan) if plan else None,
        "preview": None if plan else plans.preview_plan(session, user, now),
        "carried_plan": plans.plan_dto(session, carried) if carried else None,
        "xp": {"total": xp, "level": rules.level_for(xp, cfg), "into_level": xp % cfg.xp["level_size"], "level_size": cfg.xp["level_size"], "today": day_xp},
        "streak_days": streak_days(session, user, today), "recent": recent, "modes": modes, "assessments": checks,
        "exam_label": current_profile(s["active_mode"])["exam"]["label_ja"],
    }  # fmt: skip


def library(session, user: m.User, now: datetime, mode: str | None = None) -> dict:
    today = L.user_day(user, now)
    U = m.Unit
    q = select(U).where(
        U.realm == user.realm,
        U.status == "READY",
        U.invalidated_at.is_(None),
        U.superseded_by.is_(None),
        U.pool == "practice",
    )
    if mode:
        q = q.where(U.mode == mode)
    # ordered in Python (code-point order), never by the database collation: "P3A" must not move behind
    # "lecture" just because one PostgreSQL was initialised with en_US and another with C
    units = sorted(
        session.execute(q).scalars().all(), key=lambda u: (u.mode, u.part, u.difficulty_band, u.created_at, u.id)
    )
    atts: dict[str, list[m.Attempt]] = {}
    for a in session.execute(
        select(m.Attempt)
        .where(m.Attempt.user_id == user.id, m.Attempt.status == "submitted")
        .order_by(m.Attempt.submitted_at)
    ).scalars():
        atts.setdefault(a.unit_id, []).append(a)
    scheds = {
        r.unit_id: r
        for r in session.execute(
            select(m.ReviewSchedule).where(m.ReviewSchedule.user_id == user.id, m.ReviewSchedule.kind == "recall")
        ).scalars()
    }
    radio_units = set(
        session.execute(
            select(m.ExposureEvent.unit_id)
            .where(m.ExposureEvent.user_id == user.id, m.ExposureEvent.kind.in_(("radio_access", "radio_play")))
            .distinct()
        ).scalars()
    )
    cards = []
    for u in units:
        al = atts.get(u.id, [])
        sc = scheds.get(u.id)
        status = "new"
        badge = None
        if any(a.category == "first" and a.result["correct_count"] == a.result["total"] for a in al):
            badge = "perfect_first"  # rainbow: first attempt, official conditions, everything correct
        elif any(L.is_unassisted_review(a) and a.result["correct_count"] == a.result["total"] for a in al):
            badge = "perfect_review"  # purple: all correct on a due review
        if sc and sc.due_day <= today:
            status = "review_due"
        elif sc:
            status = "review_scheduled"
        elif al:
            status = "learned"
        elif u.id in radio_units:
            status = "radio_heard"
        cards.append({
            **plans.unit_card(u), "topic": u.topic, "skill_tags": u.skill_tags, "session_types": u.session_types, "status": status, "status_label": STATUS_LABELS[status],
            "badge": badge, "badge_label": STATUS_LABELS[badge] if badge else None, "due_day": sc.due_day.isoformat() if sc else None, "stage": sc.stage if sc else None,
            "attempts": len(al), "last_score": f"{al[-1].result['correct_count']}/{al[-1].result['total']}" if al else None,
            "presentation": u.presentation_profile.get("kind", "official"), "calibration_status": u.calibration_status, "ready_at": u.ready_at.isoformat() if u.ready_at else None,
        })  # fmt: skip
    return {"units": cards, "count": len(cards), "generated_at": now.isoformat()}


def _agg(rows: list[m.Attempt]) -> dict:
    n = len(rows)
    q_total = sum(a.result["total"] for a in rows)
    q_ok = sum(a.result["correct_count"] for a in rows)
    mean = sum(a.unit_score for a in rows if a.unit_score is not None) / n if n else None
    return {
        "units": n,
        "questions": q_total,
        "correct": q_ok,
        "unit_mean": round(mean, 4) if mean is not None else None,
        "note": "判定中（参考値）" if 0 < n < 5 else ("データなし" if n == 0 else None),
    }


def repair_outcomes(session, user, mode):
    from lq.services.repair import outcomes

    return outcomes(session, user, mode)


def short_checks(session, user: m.User, mode: str, limit: int = 10) -> dict:
    """L04: each short check with its conditions and counts. Only unaided first answers count; a few questions are a
    reference value, never a change of ability by themselves."""
    out = []
    plans_ = session.execute(
        select(m.DailyPlan).where(m.DailyPlan.user_id == user.id, m.DailyPlan.mode == mode, m.DailyPlan.params["check"].is_not(None))
        .order_by(m.DailyPlan.learning_day.desc()).limit(limit)
    ).scalars().all()  # fmt: skip
    for p in plans_:
        chk = p.params["check"]
        slots = (
            session.execute(select(m.PlanSlot).where(m.PlanSlot.plan_id == p.id, m.PlanSlot.category == "check"))
            .scalars()
            .all()
        )
        q = c = done = excluded = 0
        for sl in slots:
            att = session.get(m.Attempt, sl.attempt_id) if sl.attempt_id else None
            if att is None or att.status != "submitted":
                continue
            done += 1
            if not att.strict_first:
                excluded += 1  # assisted / partial / already met: kept out of the check series
                continue
            q += att.result["total"]
            c += att.result["correct_count"]
        out.append({"id": chk["id"], "day": p.learning_day.isoformat(), "conditions": chk["conditions"], "units_done": done, "units_planned": len(slots),
                    "questions": q, "correct": c, "excluded_units": excluded, "reference_only": True})  # fmt: skip
    return {
        "label": "短時間チェック（未見・本番形式の再生条件・参考値）",
        "checks": out,
        "note": "問題数が少ないため、1回の上下を実力の変化と断定しません。公式フル模試・予測得点・CEFR 認定ではありません。",
    }


def _practice_agg(atts: list[m.Attempt]) -> dict:
    res = [(a.result or {}).get("practice") or {} for a in atts]
    first = [r["first_understanding"] for r in res if r.get("first_understanding") is not None]
    after = [r["task_success"] for r in res if r.get("clarifications") and r.get("task_success") is not None]
    return {
        "label": "実践課題（試験形式とは別集計・級や CEFR の認定ではありません）", "tasks": len(atts),
        "evidence_tasks": sum(1 for r in res if r.get("evidence_eligible")),
        "first_mean": round(sum(first) / len(first), 3) if first else None,
        "after_check_mean": round(sum(after) / len(after), 3) if after else None,
        "critical": sum(1 for r in res if r.get("critical_first")),
    }  # fmt: skip


def growth(session, user: m.User, now: datetime, mode: str, days: int = 30) -> dict:
    cfg = rules.load_config()
    since = now - timedelta(days=days)
    rows = [(a, u) for a, u in _valid_attempts(session, user, mode, since) if u.invalidated_at is None]
    excluded = sum(1 for a, u in _valid_attempts(session, user, mode, since) if u.invalidated_at is not None)
    every = [a for a, _ in rows if a.session_type in ("train", "review")]
    # practice tasks are their own track (統合指示書 A01 / M01): never mixed into exam-format figures
    practice_atts = [a for a in every if a.presentation == "practice"]
    answer = [a for a in every if a.presentation != "practice"]
    first_official = [a for a in answer if a.category == "first" and a.presentation == "official"]
    first_arranged = [a for a in answer if a.category == "first" and a.presentation != "official"]
    review = [a for a in answer if L.is_unassisted_review(a)]
    assisted = [a for a in answer if a.result.get("assisted") and a.complete]
    st = L.get_mode_state(session, user.id, mode)
    band_obs = L.band_observations(session, user.id, mode, st.band_since)
    distinct = len({fp for fp, _ in band_obs})
    SO = m.SkillObservation
    obs = session.execute(
        select(SO.skill, SO.score, SO.created_at)
        .join(m.Unit, m.Unit.id == SO.unit_id)
        .where(SO.user_id == user.id, SO.mode == mode, SO.kind == "strict_first", m.Unit.invalidated_at.is_(None))
        .order_by(SO.created_at.desc())
    ).all()
    skills = []
    for sk in SKILLS:
        vals = [o for o in obs if o[0] == sk][: cfg.skill_window]
        n = len(vals)
        mean = sum(v[1] for v in vals) / n if n else None
        status = rules.skill_status(n, mean, cfg)
        skills.append({
            "skill": sk, "label": SKILL_LABELS_JA[sk], "n": n, "mean": round(mean, 4) if mean is not None else None, "status": status,
            "status_label": {"unmeasured": "未測定", "reference": "参考値（判定中）", "weak": "重点練習の対象", "developing": "伸長中", "strong": "安定"}[status],
            "trend": [round(v[1], 3) for v in reversed(vals)], "window": cfg.skill_window, "min_observations": cfg.skill_min_observations,
        })  # fmt: skip
    reports = session.execute(
        select(m.SelfReport.value, func.count())
        .join(m.Unit, m.Unit.id == m.SelfReport.unit_id)
        .where(m.SelfReport.user_id == user.id, m.Unit.mode == mode, m.SelfReport.created_at >= since)
        .group_by(m.SelfReport.value)
    ).all()
    progs = (
        session.execute(
            select(m.RadioProgram).where(
                m.RadioProgram.user_id == user.id, m.RadioProgram.mode == mode, m.RadioProgram.created_at >= since
            )
        )
        .scalars()
        .all()
    )
    radio_ms = sum(b - a for p in progs for a, b in p.played_ranges)
    radio_units = len({uid for p in progs for uid in p.unit_ids})
    assess = (
        session.execute(
            select(m.LearningSession)
            .where(
                m.LearningSession.user_id == user.id,
                m.LearningSession.session_type == "assessment",
                m.LearningSession.mode == mode,
                m.LearningSession.status == "submitted",
            )
            .order_by(m.LearningSession.ended_at.desc())
            .limit(5)
        )
        .scalars()
        .all()
    )
    xp = int(
        session.execute(
            select(func.coalesce(func.sum(m.XpEvent.amount), 0)).where(
                m.XpEvent.user_id == user.id, m.XpEvent.created_at >= since
            )
        ).scalar_one()
    )
    # what to practise next: weak skills first (lowest mean), then skills with too few observations to judge
    rank = {"weak": 0, "reference": 1, "unmeasured": 2, "developing": 3, "strong": 4}
    focus = []
    for sk in sorted(skills, key=lambda x: (rank[x["status"]], x["mean"] if x["mean"] is not None else 1.0, x["n"])):
        if sk["status"] in ("developing", "strong") or len(focus) >= 3:
            continue
        reason = {
            "weak": f"直近の初見の平均が {round((sk['mean'] or 0) * 100)}%（{sk['n']}件）",
            "reference": f"まだ {sk['n']} 件で判定できないため、もう少し解いて確かめます",
            "unmeasured": "まだ解いていない技能です",
        }[sk["status"]]
        focus.append({"skill": sk["skill"], "label": sk["label"], "reason_ja": reason})
    # the learner's own reasons for wrong answers (self-reported, per question) in the period
    reason_counts: dict[str, int] = {}
    for a in answer:
        for r in (a.error_reasons or {}).values():
            if r:
                reason_counts[r] = reason_counts.get(r, 0) + 1
    missed_lines = session.execute(
        select(func.count()).select_from(m.MissedLine).join(m.Unit, m.Unit.id == m.MissedLine.unit_id)
        .where(m.MissedLine.user_id == user.id, m.Unit.mode == mode)
    ).scalar_one()  # fmt: skip
    daily: dict[str, dict] = {}
    for a in answer:
        d = daily.setdefault(
            a.learning_day.isoformat(),
            {"day": a.learning_day.isoformat(), "units": 0, "first_units": 0, "first_sum": 0.0},
        )
        d["units"] += 1
        if a.category == "first":
            d["first_units"] += 1
            d["first_sum"] += a.unit_score or 0.0
    return {
        "mode": mode, "mode_label": MODE_LABELS_JA[mode], "period_days": days, "period_from": since.date().isoformat(), "period_to": now.date().isoformat(),
        "practice": _practice_agg(practice_atts),
        "short_checks": short_checks(session, user, mode),
        "volume": {"units": len({a.unit_id for a in every}), "answers": sum(a.result["total"] for a in every), "radio_listened_sec": radio_ms // 1000, "xp_all_modes": xp, "label": "学習量（能力の推定ではありません）"},
        "first_official": {**_agg(first_official), "label": "初見の成績（本番形式・規定回数・補助なし）"},
        "first_arranged": {**_agg(first_arranged), "label": "初見の成績（音声向けアレンジ条件）"},
        "review": {**_agg(review), "label": "既出問題の定着（期限が来た復習・補助なし）"},
        "assisted": {**_agg(assisted), "label": "補助付きの結果（追加再生・速度変更・英文表示・ヒント）"},
        "self_reported": {"counts": {v: int(n) for v, n in reports}, "radio_units": radio_units, "label": "自己申告とラジオ接触（客観採点とは別集計）"},
        "band": {"current": st.difficulty_band, "label": "モード内の暫定難度帯（1〜5）", "eligible_units_in_band": distinct, "needed": cfg.band_min_units,
                 "up_threshold": cfg.band_up_threshold, "down_threshold": cfg.band_down_threshold, "history": st.band_history[-10:],
                 "note": "設定値による調整です。研究で確定した最適値ではありません。"},
        "skills": skills, "repair": repair_outcomes(session, user, mode), "next_focus": focus, "error_reasons": reason_counts, "missed_lines": int(missed_lines), "daily": [{"day": d["day"], "units": d["units"], "first_units": d["first_units"], "first_mean": round(d["first_sum"] / d["first_units"], 3) if d["first_units"] else None} for d in sorted(daily.values(), key=lambda x: x["day"])],
        "assessments": [{"session_id": s.id, "at": s.ended_at.isoformat(), **{k: s.result.get(k) for k in ("score_kind", "points", "max_points", "correct_count", "slot_count", "by_section", "label")}} for s in assess],
        "excluded_invalidated": excluded,
        "disclaimer": "アプリ内の正答率・XPは、英検CSE・合否・CEFR認定・共通テスト得点に換算できません。",
    }  # fmt: skip


def history(session, user: m.User, limit: int = 50, offset: int = 0, mode: str | None = None) -> dict:
    A, U = m.Attempt, m.Unit
    q = select(A, U).join(U, U.id == A.unit_id).where(A.user_id == user.id, A.status == "submitted")
    if mode:
        q = q.where(A.mode == mode)
    rows = session.execute(q.order_by(A.submitted_at.desc()).limit(limit).offset(offset)).all()
    reason_ja = {"prior_exposure": "過去に接触あり", "not_first_attempt": "初回ではない", "extra_replay": "追加再生", "speed_changed": "速度変更", "transcript": "英文表示", "hint": "ヒント",
                 "prompt_text": "問いの文字表示", "answer_access_elsewhere": "別経路で解答に接触", "incomplete": "未回答あり", "invalidated": "無効化済み", "session_type:review": "復習",
                 "session_type:assessment": "実力チェック"}  # fmt: skip
    items = []
    for a, u in rows:
        items.append({
            "attempt_id": a.id, "at": a.submitted_at.isoformat(), "learning_day": a.learning_day.isoformat(), "mode": a.mode, "title": u.title, "unit_id": u.id,
            "session_type": a.session_type, "category": a.category, "presentation": a.presentation, "correct_count": a.result["correct_count"], "total": a.result["total"],
            "complete": a.complete, "counted_as_first": a.strict_first and u.invalidated_at is None, "conditions": [reason_ja.get(r, r) for r in a.not_first_reasons],
            "plays": a.plays, "aids": [k for k, v in (a.aids or {}).items() if v], "offline": a.offline, "self_reasons": a.error_reasons or {},
            "invalidated": u.invalidated_at is not None, "invalidated_reason": u.invalidated_reason, "feedback_viewed": a.feedback_viewed_at is not None,
        })  # fmt: skip
    reports = session.execute(
        select(m.SelfReport, U)
        .join(U, U.id == m.SelfReport.unit_id)
        .where(m.SelfReport.user_id == user.id)
        .order_by(m.SelfReport.created_at.desc())
        .limit(30)
    ).all()
    audits = (
        session.execute(
            select(m.AuditLog).where(m.AuditLog.realm == user.realm).order_by(m.AuditLog.created_at.desc()).limit(30)
        )
        .scalars()
        .all()
    )
    return {
        "attempts": items,
        "self_reports": [
            {
                "id": r.id,
                "at": r.created_at.isoformat(),
                "title": u.title,
                "mode": u.mode,
                "value": r.value,
                "label": {"correct": "自分では正解", "incorrect": "自分では不正解", "unknown": "不明"}[r.value],
            }
            for r, u in reports
        ],
        "corrections": [
            {"at": x.created_at.isoformat(), "action": x.action, "target": x.target, "details": x.details}
            for x in audits
        ],
    }


def schedules(session, user: m.User, now: datetime) -> list[dict]:
    today = L.user_day(user, now)
    cfg = rules.load_config()
    rows = session.execute(
        select(m.ReviewSchedule, m.Unit)
        .join(m.Unit, m.Unit.id == m.ReviewSchedule.unit_id)
        .where(m.ReviewSchedule.user_id == user.id, m.Unit.invalidated_at.is_(None))
        .order_by(m.ReviewSchedule.due_day)
    ).all()
    return [{"unit_id": u.id, "title": u.title, "mode": u.mode, "kind": r.kind, "stage": r.stage, "interval_days": cfg.review_intervals_days[r.stage], "due_day": r.due_day.isoformat(), "due": r.due_day <= today,
             "last_result": r.last_result, "last_change": (r.history or [{}])[-1], "history": r.history[-8:]} for r, u in rows]  # fmt: skip


def recompute_mode_state(session, user: m.User, mode: str) -> dict:
    """Replay the band rule over valid strict-first results (used after invalidation / restore)."""
    cfg = rules.load_config()
    A, U = m.Attempt, m.Unit
    rows = session.execute(
        select(A.stimulus_fingerprint, A.unit_score, A.learning_day, A.submitted_at).join(U, U.id == A.unit_id)
        .where(A.user_id == user.id, A.mode == mode, A.strict_first.is_(True), A.complete.is_(True), A.status == "submitted", U.invalidated_at.is_(None)).order_by(A.submitted_at)
    ).all()  # fmt: skip
    band, since_idx, last_day, hist = cfg.initial_band, 0, None, []
    band_since = datetime(2000, 1, 1, tzinfo=UTC)
    for i, (_fp, _score, day, at) in enumerate(rows):
        window = [(r[0], float(r[1])) for r in rows[since_idx : i + 1]]
        d = rules.adjust_band(band, window, last_day, day, cfg)
        if d.changed:
            hist.append(
                {
                    "at": at.isoformat(),
                    "day": day.isoformat(),
                    "from": band,
                    "to": d.band,
                    "reason": d.reason,
                    "n": d.n,
                    "mean": round(d.mean, 4),
                    "recomputed": True,
                }
            )
            band, since_idx, last_day, band_since = d.band, i + 1, day, at
    st = L.get_mode_state(session, user.id, mode, lock=True)
    st.difficulty_band, st.band_since, st.last_band_change_day, st.band_history = band, band_since, last_day, hist
    return {"mode": mode, "band": band, "changes": len(hist)}

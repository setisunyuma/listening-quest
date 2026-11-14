"""Notifications sent to the learner's devices (Web Push). Run by the worker every few minutes.

- reminder: once a learning day, at the chosen local hour, if today's quest is not completed
- new material: at most once a day, when units became READY since the last notice
- problems: generation failures/quarantines and budget stops, at most once per 6 hours
Nothing is sent to a user who has not subscribed a device; each kind can be switched off in settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from lq import push
from lq.db import models as m
from lq.services import learning as L

PROBLEM_EVERY = timedelta(hours=6)


def _state(session, user: m.User) -> m.NotificationState:
    st = session.get(m.NotificationState, user.id)
    if st is None:
        st = m.NotificationState(user_id=user.id, data={})
        session.add(st)
        session.flush()
    return st


def send_all(session, user: m.User, message: dict) -> dict:
    subs = session.execute(select(m.PushSubscription).where(m.PushSubscription.user_id == user.id)).scalars().all()
    out = {"ok": 0, "gone": 0, "error": 0}
    for sub in subs:
        out[push.send(session, sub, message)] += 1
    return out


def due_messages(session, user: m.User, now: datetime) -> list[tuple[str, dict]]:
    """(kind, message) pairs that should go out now. Pure decision; recording happens in run_for_user."""
    s = L.user_settings(user)
    data = dict(_state(session, user).data or {})
    out: list[tuple[str, dict]] = []
    day = L.user_day(user, now)
    hour = s.get("notify_reminder_hour")
    if (
        hour is not None
        and now.astimezone(ZoneInfo(s["timezone"])).hour == int(hour)
        and data.get("reminder_day") != day.isoformat()
    ):
        done = session.execute(
            select(func.count()).select_from(m.DailyPlan).where(
                m.DailyPlan.user_id == user.id, m.DailyPlan.learning_day == day, m.DailyPlan.kind == "quest", m.DailyPlan.status == "completed"
            )
        ).scalar_one()  # fmt: skip
        if not done:
            out.append(
                (
                    "reminder",
                    {
                        "title": "今日のクエスト",
                        "body": "今日のクエストがまだ終わっていません。数分だけでも聞いてみましょう。",
                        "url": "/",
                    },
                )
            )
    if s.get("notify_new_material", True) and data.get("material_day") != day.isoformat():
        since = (
            datetime.fromisoformat(data["material_since"]) if data.get("material_since") else now - timedelta(days=1)
        )
        n = session.execute(
            select(func.count()).select_from(m.Unit).where(m.Unit.realm == user.realm, m.Unit.status == "READY", m.Unit.ready_at > since, m.Unit.pool == "practice")
        ).scalar_one()  # fmt: skip
        if n:
            out.append(
                ("material", {"title": "新しい教材", "body": f"新しい教材が {n} 件できました。", "url": "/library"})
            )
    if s.get("notify_problems", True):
        last = datetime.fromisoformat(data["problem_at"]) if data.get("problem_at") else now - timedelta(days=1)
        if now - last >= PROBLEM_EVERY:
            J, U = m.GenerationJob, m.Unit
            bad = session.execute(
                select(func.count()).select_from(J).outerjoin(U, U.id == J.unit_id)
                .where(J.realm == user.realm, J.updated_at > last, (J.status == "FAILED") | (U.status == "QUARANTINED"))
            ).scalar_one()  # fmt: skip
            blocked = session.execute(
                select(J.blocked_reason).where(J.realm == user.realm, J.status == "BLOCKED", J.updated_at > last,
                                               J.blocked_reason.in_(("budget", "monthly_budget")))
            ).scalars().first()  # fmt: skip
            parts = []
            if bad:
                parts.append(f"生成で失敗・隔離が {bad} 件")
            if blocked:
                parts.append(
                    "月の予算に達したため生成を止めています"
                    if blocked == "monthly_budget"
                    else "今日の予算に達したため生成を止めています"
                )
            if parts:
                out.append(
                    (
                        "problem",
                        {
                            "title": "教材の生成",
                            "body": "。".join(parts) + "。教材管理で確認できます。",
                            "url": "/admin",
                        },
                    )
                )
    return out


def run_for_user(session, user: m.User, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    if not session.execute(select(m.PushSubscription.id).where(m.PushSubscription.user_id == user.id)).first():
        return []
    st = _state(session, user)
    data = dict(st.data or {})
    sent = []
    for kind, msg in due_messages(session, user, now):
        res = send_all(session, user, msg)
        if res["ok"]:
            sent.append(kind)
        day = L.user_day(user, now).isoformat()
        # record even when every device failed: no retry storm; the next period tries again
        if kind == "reminder":
            data["reminder_day"] = day
        elif kind == "material":
            data["material_day"], data["material_since"] = day, now.isoformat()
        elif kind == "problem":
            data["problem_at"] = now.isoformat()
    st.data = data
    return sent


def run_all(session, now: datetime | None = None) -> int:
    users = (
        session.execute(select(m.User).join(m.PushSubscription, m.PushSubscription.user_id == m.User.id).distinct())
        .scalars()
        .all()
    )
    return sum(len(run_for_user(session, u, now)) for u in users)

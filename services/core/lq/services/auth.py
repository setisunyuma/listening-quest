"""Single-owner authentication: argon2 password hashes, server-side sessions in an HttpOnly cookie,
CSRF token (double submit), login attempt limiting. No public sign-up."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select

from lq.config import get_settings
from lq.db import models as m

_ph = PasswordHasher()
SESSION_COOKIE = "lq_session"
CSRF_COOKIE = "lq_csrf"


class TooManyAttempts(Exception):
    pass


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    return _ph.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


def create_user(session, username: str, password: str, realm: str = "live") -> m.User:
    if realm not in ("live", "demo"):
        raise ValueError("realm must be live or demo")
    if session.execute(select(m.User).where(m.User.username == username)).scalar_one_or_none():
        raise ValueError("user already exists")
    if (
        realm == "live"
        and session.execute(select(func.count()).select_from(m.User).where(m.User.realm == "live")).scalar_one() >= 1
    ):
        raise ValueError("this app is for one owner account; a live account already exists")
    user = m.User(username=username, password_hash=hash_password(password), realm=realm, settings={})
    session.add(user)
    session.flush()
    return user


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(
    session, username: str, password: str, client_key: str, now: datetime | None = None
) -> tuple[m.User, str, str]:
    s = get_settings()
    now = now or datetime.now(UTC)
    since = now - timedelta(minutes=s.login_window_minutes)
    for key in (f"u:{username.lower()}", f"ip:{client_key}"):
        fails = session.execute(
            select(func.count())
            .select_from(m.LoginAttempt)
            .where(m.LoginAttempt.key == key, m.LoginAttempt.success.is_(False), m.LoginAttempt.at >= since)
        ).scalar_one()
        if fails >= s.login_max_failures:
            raise TooManyAttempts()
    user = session.execute(select(m.User).where(m.User.username == username)).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else _DUMMY, password) and user is not None
    for key in (f"u:{username.lower()}", f"ip:{client_key}"):
        session.add(m.LoginAttempt(key=key, success=ok, at=now))
    if not ok:
        return None, "", ""  # type: ignore[return-value]
    session.execute(
        delete(m.LoginAttempt).where(m.LoginAttempt.key == f"u:{username.lower()}", m.LoginAttempt.success.is_(False))
    )
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    session.add(
        m.AuthSession(
            token_hash=_tok_hash(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=now + timedelta(hours=s.session_ttl_hours),
        )
    )
    session.execute(delete(m.AuthSession).where(m.AuthSession.expires_at < now))
    return user, token, csrf


_DUMMY = _ph.hash("not-a-real-password-placeholder")


def resolve(session, token: str | None, now: datetime | None = None) -> tuple[m.User, m.AuthSession] | None:
    if not token:
        return None
    now = now or datetime.now(UTC)
    row = session.get(m.AuthSession, _tok_hash(token))
    if row is None or row.expires_at < now:
        return None
    user = session.get(m.User, row.user_id)
    return (user, row) if user else None


def logout(session, token: str | None) -> None:
    if token:
        session.execute(delete(m.AuthSession).where(m.AuthSession.token_hash == _tok_hash(token)))

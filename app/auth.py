"""Пароли и серверные сессии для входа."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthSession, Role, User

password_hash = PasswordHash.recommended()
COOKIE_NAME = "session"
SESSION_SECONDS = 8 * 60 * 60
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(password, hashed)
    except (UnknownHashError, ValueError):
        # Повреждённый или неизвестный формат хеша не должен приводить к ошибке 500.
        return False


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_session(db: Session, user: User | None = None) -> tuple[AuthSession, str]:
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        token_digest=digest(token),
        user=user,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(seconds=SESSION_SECONDS),
    )
    db.add(session)
    return session, token


def get_session(request: Request, db: Session) -> AuthSession | None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not TOKEN_PATTERN.fullmatch(token):
        return None
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_digest == digest(token))
    )
    if session is None:
        return None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return None
    return session


def check_csrf(session: AuthSession | None, token: str) -> None:
    if (
        session is None
        or not TOKEN_PATTERN.fullmatch(token)
        or not hmac.compare_digest(session.csrf_token, token)
    ):
        raise HTTPException(status_code=403, detail="Недействительный токен формы")


def require_role(session: AuthSession | None, role: Role) -> User | None:
    if session is None or session.user is None:
        return None
    if session.user.role != role:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return session.user


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )

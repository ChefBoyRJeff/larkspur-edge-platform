"""Sign-in sessions stored in the database, so signing out or deactivating a user ends them.

The browser keeps a random token in a cookie. Only the token's SHA-256 is stored, so someone who
reads the table cannot sign in as anyone. A session ends 8 hours after sign-in, or after 60
minutes without a page view, whichever comes first.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from larkspur_track.models import User, UserSession

SESSION_LIFETIME = timedelta(hours=8)
IDLE_TIMEOUT = timedelta(minutes=60)
# last_seen_at is written at most once a minute, not on every page view.
TOUCH_INTERVAL = timedelta(minutes=1)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _expired(row: UserSession, now: datetime) -> bool:
    return now >= row.expires_at or now - row.last_seen_at >= IDLE_TIMEOUT


def start_session(session: Session, user: User, now: datetime) -> tuple[str, UserSession]:
    """Create a session and return the cookie token with the row. The token is never stored."""
    # Housekeeping: drop this user's sessions that have already ended.
    session.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id,
            or_(UserSession.expires_at <= now, UserSession.last_seen_at <= now - IDLE_TIMEOUT),
        )
    )
    token = secrets.token_urlsafe(32)
    row = UserSession(
        token_hash=hash_token(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_LIFETIME,
    )
    session.add(row)
    session.flush()
    return token, row


def resume_session(session: Session, token: str, now: datetime) -> UserSession | None:
    """The session for a cookie token, or None when it is unknown, ended or the user is inactive."""
    row = session.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if row is None:
        return None
    if _expired(row, now) or not row.user.is_active:
        session.delete(row)
        session.flush()
        return None
    if now - row.last_seen_at >= TOUCH_INTERVAL:
        row.last_seen_at = now
        session.flush()
    return row


def end_session(session: Session, token: str) -> None:
    session.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))

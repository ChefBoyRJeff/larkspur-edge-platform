"""Sessions: tokens are stored only as hashes, and sessions end when idle, old or deactivated."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import MakeUser
from larkspur_track.models import UserSession
from larkspur_track.sessions import (
    IDLE_TIMEOUT,
    SESSION_LIFETIME,
    end_session,
    hash_token,
    resume_session,
    start_session,
)

T0 = datetime(2026, 11, 24, 14, 0, tzinfo=UTC)


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(UserSession)) or 0


def test_only_the_token_hash_is_stored(db_session: Session, make_user: MakeUser) -> None:
    token, row = start_session(db_session, make_user(), T0)

    assert row.token_hash == hash_token(token)
    assert token not in (row.token_hash, row.csrf_token)
    assert row.expires_at == T0 + SESSION_LIFETIME


def test_resume_finds_the_session(db_session: Session, make_user: MakeUser) -> None:
    token, row = start_session(db_session, make_user(), T0)

    assert resume_session(db_session, token, T0 + timedelta(minutes=5)) is row


def test_unknown_token_finds_nothing(db_session: Session, make_user: MakeUser) -> None:
    start_session(db_session, make_user(), T0)

    assert resume_session(db_session, "not-a-real-token", T0) is None


def test_idle_session_ends_and_is_removed(db_session: Session, make_user: MakeUser) -> None:
    token, _ = start_session(db_session, make_user(), T0)

    assert resume_session(db_session, token, T0 + IDLE_TIMEOUT) is None
    assert _count(db_session) == 0


def test_last_seen_is_written_at_most_once_a_minute(
    db_session: Session, make_user: MakeUser
) -> None:
    token, row = start_session(db_session, make_user(), T0)

    resume_session(db_session, token, T0 + timedelta(seconds=30))
    assert row.last_seen_at == T0

    resume_session(db_session, token, T0 + timedelta(minutes=2))
    assert row.last_seen_at == T0 + timedelta(minutes=2)


def test_active_session_still_ends_after_eight_hours(
    db_session: Session, make_user: MakeUser
) -> None:
    token, _ = start_session(db_session, make_user(), T0)
    now = T0
    while now < T0 + SESSION_LIFETIME - timedelta(minutes=30):
        now += timedelta(minutes=30)
        assert resume_session(db_session, token, now) is not None

    assert resume_session(db_session, token, T0 + SESSION_LIFETIME) is None


def test_deactivated_user_loses_their_session(db_session: Session, make_user: MakeUser) -> None:
    user = make_user()
    token, _ = start_session(db_session, user, T0)

    user.is_active = False
    db_session.flush()

    assert resume_session(db_session, token, T0 + timedelta(minutes=1)) is None


def test_end_session_removes_it(db_session: Session, make_user: MakeUser) -> None:
    token, _ = start_session(db_session, make_user(), T0)

    end_session(db_session, token)

    assert resume_session(db_session, token, T0) is None
    assert _count(db_session) == 0


def test_signing_in_clears_the_users_ended_sessions(
    db_session: Session, make_user: MakeUser
) -> None:
    user = make_user()
    start_session(db_session, user, T0)
    start_session(db_session, user, T0 + timedelta(minutes=30))

    start_session(db_session, user, T0 + timedelta(minutes=75))

    # The first session was idle for 75 minutes and is removed; the second (45) is kept.
    assert _count(db_session) == 2

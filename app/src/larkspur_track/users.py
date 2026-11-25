"""User accounts. Functions flush but never commit; the caller owns the transaction."""

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from larkspur_track.models import User
from larkspur_track.passwords import hash_password, password_problem
from larkspur_track.roles import UserRole
from larkspur_track.sessions import end_user_sessions

USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,39}")
DISPLAY_NAME_MAX = 120


class UserInputError(ValueError):
    """Raised with a message the person entering the data can act on."""


def normalize_username(username: str) -> str:
    """Usernames are case-insensitive: compare and store them trimmed and in lower case."""
    return username.strip().lower()


def find_user(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == normalize_username(username)))


def create_user(
    session: Session, *, username: str, display_name: str, role: UserRole, password: str
) -> User:
    """Create an active user with a hashed password, after checking every field."""
    name = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(name):
        raise UserInputError(
            "Use 3 to 40 letters, digits, dots, dashes or underscores for the username, "
            "starting with a letter or digit."
        )
    display = display_name.strip()
    if not display or len(display) > DISPLAY_NAME_MAX:
        raise UserInputError(f"Enter a display name of 1 to {DISPLAY_NAME_MAX} characters.")
    problem = password_problem(password)
    if problem:
        raise UserInputError(problem)
    if find_user(session, name) is not None:
        raise UserInputError(f"The username {name} is already taken.")
    user = User(
        username=name, display_name=display, role=role, password_hash=hash_password(password)
    )
    session.add(user)
    session.flush()
    return user


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username)))


# An admin cannot demote or deactivate themselves. Only admins change users, so there is always
# at least one active admin left to undo a mistake.
def change_role(session: Session, user: User, role: UserRole, *, by: uuid.UUID) -> None:
    if user.id == by:
        raise UserInputError("You can't change your own role. Ask another admin.")
    user.role = role
    session.flush()


def set_active(session: Session, user: User, active: bool, *, by: uuid.UUID) -> None:
    """Deactivating keeps the user (and later their history) but signs them out everywhere."""
    if user.id == by and not active:
        raise UserInputError("You can't deactivate your own account. Ask another admin.")
    user.is_active = active
    if not active:
        end_user_sessions(session, user.id)
    session.flush()


def reset_password(session: Session, user: User, password: str, now: datetime) -> None:
    """Set a new password, clear any lockout, and end the user's sessions."""
    problem = password_problem(password)
    if problem:
        raise UserInputError(problem)
    user.password_hash = hash_password(password)
    user.password_changed_at = now
    user.failed_sign_ins = 0
    user.locked_until = None
    end_user_sessions(session, user.id)
    session.flush()


def unlock(session: Session, user: User) -> None:
    """End a lockout from too many wrong passwords."""
    user.failed_sign_ins = 0
    user.locked_until = None
    session.flush()


def is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now

"""User accounts. Functions flush but never commit; the caller owns the transaction."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from larkspur_track.models import User
from larkspur_track.passwords import hash_password, password_problem
from larkspur_track.roles import UserRole

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

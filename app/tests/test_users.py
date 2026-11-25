"""Creating users: validation, normalization, hashing and the database constraints."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from larkspur_track.models import User
from larkspur_track.passwords import verify_password
from larkspur_track.roles import UserRole
from larkspur_track.users import UserInputError, create_user, find_user

PHRASE = "correct horse battery"
SHORT = "too-short"


def _create(
    session: Session,
    username: str = "jdoe",
    display_name: str = "Jane Doe",
    role: UserRole = UserRole.CLERK,
    password: str = PHRASE,
) -> User:
    return create_user(
        session, username=username, display_name=display_name, role=role, password=password
    )


def test_create_user_normalizes_and_hashes(db_session: Session) -> None:
    user = _create(db_session, username="  JDoe ", display_name="  Jane Doe ")

    assert user.username == "jdoe"
    assert user.display_name == "Jane Doe"
    assert user.role is UserRole.CLERK
    assert user.is_active
    assert user.failed_sign_ins == 0
    assert user.locked_until is None
    assert user.password_hash != PHRASE
    assert verify_password(user.password_hash, PHRASE)


def test_find_user_ignores_case(db_session: Session) -> None:
    user = _create(db_session)

    found = find_user(db_session, " JDOE ")

    assert found is not None
    assert found.id == user.id


def test_duplicate_username_is_rejected(db_session: Session) -> None:
    _create(db_session)

    with pytest.raises(UserInputError, match="already taken"):
        _create(db_session, username="JDOE")


@pytest.mark.parametrize("username", ["ab", "-jdoe", "j doe", "x" * 41, "jdoe@site"])
def test_invalid_usernames_are_rejected(db_session: Session, username: str) -> None:
    with pytest.raises(UserInputError, match="username"):
        _create(db_session, username=username)


def test_blank_display_name_is_rejected(db_session: Session) -> None:
    with pytest.raises(UserInputError, match="display name"):
        _create(db_session, display_name="   ")


def test_short_password_is_rejected(db_session: Session) -> None:
    with pytest.raises(UserInputError, match="at least 12"):
        _create(db_session, password=SHORT)


def test_database_rejects_unknown_roles(db_session: Session) -> None:
    """The CHECK constraint guards the role column even if code is bypassed."""
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO users (id, username, display_name, role, password_hash, "
                "is_active, failed_sign_ins) "
                "VALUES (gen_random_uuid(), 'mallory', 'Mallory', 'owner', 'x', true, 0)"
            )
        )

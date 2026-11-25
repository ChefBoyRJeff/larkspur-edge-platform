"""larkspur-track-admin: creating users from the command line."""

from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from larkspur_track.admin import build_parser, run_create_user
from larkspur_track.roles import UserRole
from larkspur_track.users import find_user

PHRASE = "correct horse battery"


def _answers(*values: str) -> Callable[[str], str]:
    """A stand-in for getpass that returns the given answers in order."""
    replies = iter(values)
    return lambda _prompt: next(replies)


def _args(*extra: str):
    return build_parser().parse_args(
        ["create-user", "--username", "Site.Admin", "--display-name", "Site Admin", *extra]
    )


def test_create_user_command(db_session: Session) -> None:
    user = run_create_user(db_session, _args("--role", "admin"), _answers(PHRASE, PHRASE))

    assert user is not None
    assert user.username == "site.admin"
    assert user.role is UserRole.ADMIN


def test_mismatched_passwords_create_nothing(
    db_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    user = run_create_user(db_session, _args("--role", "clerk"), _answers(PHRASE, PHRASE + "!"))

    assert user is None
    assert "do not match" in capsys.readouterr().err
    assert find_user(db_session, "site.admin") is None


def test_rule_violations_are_reported(
    db_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    user = run_create_user(db_session, _args("--role", "clerk"), _answers("short", "short"))

    assert user is None
    assert "at least 12" in capsys.readouterr().err


def test_unknown_role_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        _args("--role", "owner")

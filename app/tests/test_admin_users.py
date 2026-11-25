"""User administration: add users, change roles, deactivate, reset passwords and unlock."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import PHRASE, MakeUser, SignedInClient
from larkspur_track.auth import authenticate
from larkspur_track.models import User
from larkspur_track.roles import UserRole
from larkspur_track.users import find_user

SignInAs = Callable[[User], SignedInClient]
NEW_PHRASE = "a brand new phrase here"
NEW_USER = {
    "username": "Dee.Dock",
    "display_name": "Dee Dock",
    "role": "clerk",
    "password": NEW_PHRASE,
    "confirm": NEW_PHRASE,
}


@pytest.fixture
def admin_user(make_user: MakeUser) -> User:
    return make_user("ada.admin", UserRole.ADMIN)


@pytest.fixture
def admin(admin_user: User, sign_in_as: SignInAs) -> SignedInClient:
    return sign_in_as(admin_user)


def _users(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User)) or 0


def test_users_page_lists_everyone(admin: SignedInClient, make_user: MakeUser) -> None:
    make_user("old.clerk", UserRole.CLERK, is_active=False)

    page = admin.get("/admin/users")

    assert "ada.admin" in page.text
    assert "old.clerk" in page.text
    assert "deactivated" in page.text


def test_admin_adds_a_user(admin: SignedInClient, db_session: Session) -> None:
    response = admin.post("/admin/users", data=NEW_USER)

    user = find_user(db_session, "dee.dock")
    assert user is not None
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/users/{user.id}?done=created"
    assert user.role is UserRole.CLERK
    assert authenticate(db_session, "dee.dock", NEW_PHRASE, datetime.now(UTC)) is user
    assert "User created." in admin.get(response.headers["location"]).text


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"confirm": NEW_PHRASE + "!"}, "The two passwords are different."),
        ({"password": "too-short", "confirm": "too-short"}, "at least 12 characters"),
        ({"role": "owner"}, "Choose a role."),
        ({"username": "x"}, "3 to 40 letters"),
    ],
)
def test_invalid_new_user_is_refused(
    admin: SignedInClient, db_session: Session, changes: dict[str, str], message: str
) -> None:
    before = _users(db_session)

    response = admin.post("/admin/users", data={**NEW_USER, **changes})

    assert response.status_code == 422
    assert message in response.text
    assert 'value="Dee Dock"' in response.text
    assert NEW_PHRASE not in response.text
    assert _users(db_session) == before


def test_taken_username_is_refused(admin: SignedInClient, make_user: MakeUser) -> None:
    make_user("dee.dock", UserRole.CLERK)

    response = admin.post("/admin/users", data=NEW_USER)

    assert response.status_code == 422
    assert "already taken" in response.text


def test_role_change_applies_on_the_next_page(
    admin: SignedInClient, make_user: MakeUser, sign_in_as: SignInAs
) -> None:
    sam = make_user()
    sam_client = sign_in_as(sam)
    assert sam_client.get("/admin/users").status_code == 403

    response = admin.post(f"/admin/users/{sam.id}/role", data={"role": "admin"})

    assert response.headers["location"] == f"/admin/users/{sam.id}?done=role"
    assert sam_client.get("/admin/users").status_code == 200


def test_admin_cannot_change_their_own_role(admin: SignedInClient, admin_user: User) -> None:
    page = admin.get(f"/admin/users/{admin_user.id}")
    response = admin.post(f"/admin/users/{admin_user.id}/role", data={"role": "clerk"})

    assert "This is your account." in page.text
    assert response.status_code == 422
    assert "can&#39;t change your own role" in response.text
    assert admin_user.role is UserRole.ADMIN


def test_deactivating_signs_the_user_out(
    admin: SignedInClient, make_user: MakeUser, sign_in_as: SignInAs, db_session: Session
) -> None:
    sam = make_user()
    sam_client = sign_in_as(sam)

    response = admin.post(f"/admin/users/{sam.id}/active", data={"active": "false"})
    db_session.refresh(sam)

    assert response.headers["location"] == f"/admin/users/{sam.id}?done=deactivated"
    assert not sam.is_active
    assert sam_client.get("/shipments").status_code == 303
    assert authenticate(db_session, "sam.supervisor", PHRASE, datetime.now(UTC)) is None


def test_reactivating_lets_the_user_sign_in(
    admin: SignedInClient, make_user: MakeUser, db_session: Session
) -> None:
    sam = make_user(is_active=False)

    admin.post(f"/admin/users/{sam.id}/active", data={"active": "true"})
    db_session.refresh(sam)

    assert sam.is_active
    assert authenticate(db_session, "sam.supervisor", PHRASE, datetime.now(UTC)) is sam


def test_admin_cannot_deactivate_themselves(
    admin: SignedInClient, admin_user: User, db_session: Session
) -> None:
    response = admin.post(f"/admin/users/{admin_user.id}/active", data={"active": "false"})
    db_session.refresh(admin_user)

    assert response.status_code == 422
    assert admin_user.is_active
    assert admin.get("/shipments").status_code == 200


def test_password_reset_signs_out_and_unlocks(
    admin: SignedInClient, make_user: MakeUser, sign_in_as: SignInAs, db_session: Session
) -> None:
    sam = make_user()
    sam_client = sign_in_as(sam)
    sam.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.flush()

    response = admin.post(
        f"/admin/users/{sam.id}/password", data={"password": NEW_PHRASE, "confirm": NEW_PHRASE}
    )
    db_session.refresh(sam)
    now = datetime.now(UTC)

    assert response.headers["location"] == f"/admin/users/{sam.id}?done=password"
    assert sam.locked_until is None
    assert sam_client.get("/shipments").status_code == 303
    assert authenticate(db_session, "sam.supervisor", PHRASE, now) is None
    assert authenticate(db_session, "sam.supervisor", NEW_PHRASE, now) is sam


def test_short_reset_password_is_refused(admin: SignedInClient, make_user: MakeUser) -> None:
    sam = make_user()

    response = admin.post(
        f"/admin/users/{sam.id}/password", data={"password": "too-short", "confirm": "too-short"}
    )

    assert response.status_code == 422
    assert "at least 12 characters" in response.text


def test_admin_unlocks_a_locked_user(
    admin: SignedInClient, make_user: MakeUser, db_session: Session
) -> None:
    sam = make_user()
    sam.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    sam.failed_sign_ins = 2
    db_session.flush()
    assert "Unlock now" in admin.get(f"/admin/users/{sam.id}").text

    admin.post(f"/admin/users/{sam.id}/unlock")
    db_session.refresh(sam)

    assert sam.locked_until is None
    assert sam.failed_sign_ins == 0


def test_unknown_user_is_not_found(admin: SignedInClient) -> None:
    page = admin.get(f"/admin/users/{uuid.uuid4()}")

    assert page.status_code == 404
    assert "User not found" in page.text

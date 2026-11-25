"""Signing in and out: generic errors, lockout, safe redirects and the session cookie."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import PHRASE, MakeUser, SignedInClient
from larkspur_track.auth import (
    LOCKOUT,
    MAX_FAILED_SIGN_INS,
    SIGN_IN_FAILED,
    authenticate,
    session_cookie_name,
)
from larkspur_track.models import User, UserSession
from larkspur_track.passwords import needs_rehash, verify_password
from larkspur_track.sessions import IDLE_TIMEOUT

T0 = datetime(2026, 11, 24, 14, 0, tzinfo=UTC)
WRONG = "not the right phrase"


def _sign_in(client: TestClient, password: str = PHRASE, **extra: str) -> httpx2.Response:
    form = {"username": "Sam.Supervisor", "password": password, **extra}
    return client.post("/sign-in", data=form)


@pytest.fixture
def secure_cookies(monkeypatch: pytest.MonkeyPatch, fresh_settings: None) -> Iterator[None]:
    """The cluster's setting: Secure, __Host- cookies (app/.env turns them off locally)."""
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    yield


def test_right_password_signs_in(db_session: Session, make_user: MakeUser) -> None:
    user = make_user()
    user.failed_sign_ins = 3
    db_session.flush()

    assert authenticate(db_session, " SAM.SUPERVISOR ", PHRASE, T0) is user
    assert user.failed_sign_ins == 0


def test_unknown_and_inactive_users_are_refused(db_session: Session, make_user: MakeUser) -> None:
    make_user("old.clerk", is_active=False)

    assert authenticate(db_session, "nobody.here", PHRASE, T0) is None
    assert authenticate(db_session, "old.clerk", PHRASE, T0) is None


def test_five_wrong_passwords_lock_the_account(db_session: Session, make_user: MakeUser) -> None:
    user = make_user()
    for _ in range(MAX_FAILED_SIGN_INS):
        assert authenticate(db_session, "sam.supervisor", WRONG, T0) is None

    assert user.locked_until == T0 + LOCKOUT
    assert user.failed_sign_ins == 0
    # Even the right password is refused while locked, and that attempt is not counted.
    assert authenticate(db_session, "sam.supervisor", PHRASE, T0 + LOCKOUT / 2) is None
    assert user.failed_sign_ins == 0
    assert authenticate(db_session, "sam.supervisor", PHRASE, T0 + LOCKOUT) is user
    assert user.locked_until is None


def test_old_hash_parameters_are_upgraded_at_sign_in(
    db_session: Session, make_user: MakeUser
) -> None:
    user = make_user()
    user.password_hash = PasswordHasher(time_cost=1, memory_cost=8 * 1024).hash(PHRASE)
    db_session.flush()
    assert needs_rehash(user.password_hash)

    assert authenticate(db_session, "sam.supervisor", PHRASE, T0) is user
    assert not needs_rehash(user.password_hash)
    assert verify_password(user.password_hash, PHRASE)


def test_pages_send_anonymous_visitors_to_sign_in(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/shipments", params={"q": "lrk"})

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in?next=%2Fshipments%3Fq%3Dlrk"


def test_health_checks_need_no_sign_in(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/healthz").status_code == 200


def test_sign_in_page_shows_the_form(anonymous_client: TestClient) -> None:
    page = anonymous_client.get("/sign-in", params={"next": "/dashboard"})

    assert page.status_code == 200
    assert 'name="password"' in page.text
    assert 'value="/dashboard"' in page.text
    assert "Sign out" not in page.text


def test_sign_in_sets_a_secure_cookie_and_opens_the_page(
    secure_cookies: None, anonymous_client: TestClient, make_user: MakeUser
) -> None:
    make_user()

    response = _sign_in(anonymous_client, next="/dashboard")
    page = anonymous_client.get("/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-larkspur_session=")
    for flag in ("HttpOnly", "Max-Age=28800", "Path=/", "SameSite=lax", "Secure"):
        assert flag in cookie
    assert page.status_code == 200
    assert "Sam Supervisor (supervisor)" in page.text


def test_local_development_cookie_is_not_secure(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
    anonymous_client: TestClient,
    make_user: MakeUser,
) -> None:
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    make_user()

    cookie = _sign_in(anonymous_client).headers["set-cookie"]

    assert cookie.startswith("larkspur_session=")
    assert "Secure" not in cookie
    assert "HttpOnly" in cookie


@pytest.mark.parametrize("password", [WRONG, ""])
def test_wrong_password_shows_a_generic_error(
    anonymous_client: TestClient, make_user: MakeUser, password: str
) -> None:
    make_user()

    response = _sign_in(anonymous_client, password=password)

    assert response.status_code == 422
    assert SIGN_IN_FAILED in response.text
    assert "set-cookie" not in response.headers


def test_unknown_user_gets_the_same_error(anonymous_client: TestClient) -> None:
    response = _sign_in(anonymous_client)

    assert response.status_code == 422
    assert SIGN_IN_FAILED in response.text


def test_failed_attempts_are_kept(
    anonymous_client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user()

    _sign_in(anonymous_client, password=WRONG)
    db_session.refresh(user)

    assert user.failed_sign_ins == 1


@pytest.mark.parametrize(
    "target", ["//evil.example/", "https://evil.example/", "/\\evil.example", "shipments"]
)
def test_sign_in_never_redirects_to_another_site(
    anonymous_client: TestClient, make_user: MakeUser, target: str
) -> None:
    make_user()

    response = _sign_in(anonymous_client, next=target)

    assert response.headers["location"] == "/shipments"


def test_cross_site_sign_in_is_refused(anonymous_client: TestClient, make_user: MakeUser) -> None:
    make_user()

    response = anonymous_client.post(
        "/sign-in",
        data={"username": "sam.supervisor", "password": PHRASE},
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_signed_in_user_skips_the_sign_in_page(client: SignedInClient) -> None:
    response = client.get("/sign-in", params={"next": "/dashboard"})

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_signing_in_again_replaces_the_session(
    anonymous_client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    make_user()
    _sign_in(anonymous_client)
    first = anonymous_client.cookies.get(session_cookie_name())

    _sign_in(anonymous_client)
    second = anonymous_client.cookies.get(session_cookie_name())

    assert first and second and first != second
    assert db_session.scalar(select(func.count()).select_from(UserSession)) == 1


def test_sign_out_ends_the_session(client: SignedInClient) -> None:
    cookies = dict(client.cookies)

    response = client.post("/sign-out")
    client.cookies.update(cookies)  # a copy of the old cookie no longer works

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/shipments").status_code == 303


def test_ended_session_sends_the_browser_to_sign_in(
    client: SignedInClient, db_session: Session
) -> None:
    for row in db_session.scalars(select(UserSession)):
        row.last_seen_at -= IDLE_TIMEOUT
    db_session.flush()

    response = client.get("/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in?next=%2Fdashboard"
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_deactivated_user_is_signed_out(client: SignedInClient, db_session: Session) -> None:
    user = db_session.scalars(select(User)).one()
    user.is_active = False
    db_session.flush()

    assert client.get("/shipments").status_code == 303


def test_lockout_message_is_the_same(
    anonymous_client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user()
    user.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.flush()

    response = _sign_in(anonymous_client)

    assert response.status_code == 422
    assert SIGN_IN_FAILED in response.text

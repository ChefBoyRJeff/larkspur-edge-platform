"""The audit log: what gets recorded, that it can't be changed, and who can read it."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from conftest import PHRASE, MakeUser, SignedInClient
from larkspur_track.admin import build_parser, run_create_user
from larkspur_track.audit import AuditAction, recent_events, record
from larkspur_track.models import AuditEvent, ShipmentEvent, User
from larkspur_track.roles import UserRole

SignInAs = Callable[[User], SignedInClient]
NEW_PHRASE = "a brand new phrase here"
FORM = {
    "tracking_number": "LRK-AUDIT-0001",
    "consignee": "Acme Retail (fictional)",
    "destination": "Dock 4, Greensboro NC",
}


def _entries(session: Session, action: AuditAction) -> list[AuditEvent]:
    return recent_events(session, action=action)


def _sign_in(client: TestClient, username: str, password: str = PHRASE) -> int:
    form = {"username": username, "password": password}
    return client.post("/sign-in", data=form).status_code


@pytest.fixture
def admin_user(make_user: MakeUser) -> User:
    return make_user("ada.admin", UserRole.ADMIN)


@pytest.fixture
def admin(admin_user: User, sign_in_as: SignInAs) -> SignedInClient:
    return sign_in_as(admin_user)


def test_newest_entries_come_first_and_can_be_filtered(db_session: Session) -> None:
    record(db_session, AuditAction.SIGNED_IN, actor_username="first")
    record(db_session, AuditAction.SIGNED_OUT, actor_username="second")

    assert [e.actor_username for e in recent_events(db_session)][:2] == ["second", "first"]
    assert [e.actor_username for e in _entries(db_session, AuditAction.SIGNED_IN)] == ["first"]


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET detail = 'edited'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
def test_database_refuses_to_change_entries(db_session: Session, statement: str) -> None:
    record(db_session, AuditAction.SIGNED_IN, actor_username="someone")

    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(statement))


def test_sign_in_is_recorded_with_the_client_address(
    anonymous_client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    sam = make_user()

    assert _sign_in(anonymous_client, "Sam.Supervisor") == 303

    [entry] = _entries(db_session, AuditAction.SIGNED_IN)
    assert entry.actor_user_id == sam.id
    assert entry.actor_username == "sam.supervisor"
    assert entry.client_ip == "testclient"  # the TestClient's address; Traefik's in the cluster


def test_failed_sign_ins_are_recorded_without_the_password(
    anonymous_client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    sam = make_user()

    _sign_in(anonymous_client, "Nobody.Here", NEW_PHRASE)
    _sign_in(anonymous_client, "sam.supervisor", NEW_PHRASE)

    unknown, known = reversed(_entries(db_session, AuditAction.SIGN_IN_FAILED))
    assert (unknown.actor_user_id, unknown.actor_username) == (None, "nobody.here")
    assert (known.actor_user_id, known.actor_username) == (sam.id, "sam.supervisor")
    for entry in (unknown, known):
        assert NEW_PHRASE not in f"{entry.actor_username} {entry.detail}"


def test_lockout_shows_in_the_failed_sign_in(
    anonymous_client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    sam = make_user()
    sam.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.flush()

    _sign_in(anonymous_client, "sam.supervisor")

    [entry] = _entries(db_session, AuditAction.SIGN_IN_FAILED)
    assert entry.detail == "account locked"


def test_sign_out_is_recorded(client: SignedInClient, db_session: Session) -> None:
    client.post("/sign-out")

    [entry] = _entries(db_session, AuditAction.SIGNED_OUT)
    assert entry.actor_username == "sam.supervisor"


def test_shipment_changes_record_who_made_them(client: SignedInClient, db_session: Session) -> None:
    location = client.post("/shipments", data=FORM).headers["location"]
    client.post(f"{location}/advance", data={"target": "stored"})

    [received] = _entries(db_session, AuditAction.SHIPMENT_RECEIVED)
    [advanced] = _entries(db_session, AuditAction.SHIPMENT_ADVANCED)
    assert received.detail == "LRK-AUDIT-0001"
    assert advanced.detail == "LRK-AUDIT-0001: received → stored"
    assert f"/shipments/{advanced.target_id}" == location
    events = db_session.scalars(select(ShipmentEvent)).all()
    assert {event.actor_user_id for event in events} == {received.actor_user_id}
    assert "Sam Supervisor" in client.get(location).text


def test_user_administration_is_recorded(
    admin: SignedInClient, make_user: MakeUser, db_session: Session
) -> None:
    sam = make_user()
    sam.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.flush()
    new_user = {"username": "dee.dock", "display_name": "Dee Dock", "role": "clerk"}
    passwords = {"password": NEW_PHRASE, "confirm": NEW_PHRASE}

    admin.post("/admin/users", data={**new_user, **passwords})
    admin.post(f"/admin/users/{sam.id}/role", data={"role": "admin"})
    admin.post(f"/admin/users/{sam.id}/unlock")
    admin.post(f"/admin/users/{sam.id}/password", data=passwords)
    admin.post(f"/admin/users/{sam.id}/active", data={"active": "false"})
    admin.post(f"/admin/users/{sam.id}/active", data={"active": "true"})

    entries = list(reversed(recent_events(db_session)))
    assert [(e.action, e.detail) for e in entries] == [
        ("user_created", "dee.dock as clerk"),
        ("user_role_changed", "sam.supervisor: supervisor → admin"),
        ("user_unlocked", "sam.supervisor"),
        ("user_password_reset", "sam.supervisor"),
        ("user_deactivated", "sam.supervisor"),
        ("user_reactivated", "sam.supervisor"),
    ]
    assert {e.actor_username for e in entries} == {"ada.admin"}
    assert all(NEW_PHRASE not in (e.detail or "") for e in entries)


def test_refused_changes_are_not_recorded(
    admin: SignedInClient, admin_user: User, db_session: Session
) -> None:
    admin.post(f"/admin/users/{admin_user.id}/role", data={"role": "clerk"})

    assert recent_events(db_session) == []


def test_command_line_user_creation_is_recorded(db_session: Session) -> None:
    args = build_parser().parse_args(
        ["create-user", "--username", "site.admin", "--display-name", "Site Admin"]
        + ["--role", "admin"]
    )

    run_create_user(db_session, args, lambda _prompt: NEW_PHRASE)

    [entry] = _entries(db_session, AuditAction.USER_CREATED)
    assert entry.actor_user_id is None
    assert entry.detail == "site.admin as admin (larkspur-track-admin)"


def test_clerks_cannot_read_the_audit_log(make_user: MakeUser, sign_in_as: SignInAs) -> None:
    clerk = sign_in_as(make_user("cal.clerk", UserRole.CLERK))

    assert 'href="/audit"' not in clerk.get("/shipments").text
    assert clerk.get("/audit").status_code == 403


def test_supervisors_read_the_audit_log(client: SignedInClient, db_session: Session) -> None:
    client.post("/shipments", data=FORM)
    record(db_session, AuditAction.SIGNED_OUT, actor_username="someone.else")

    page = client.get("/audit")
    filtered = client.get("/audit", params={"action": "shipment_received"})

    assert 'href="/audit"' in client.get("/shipments").text
    assert page.status_code == 200
    assert "LRK-AUDIT-0001" in page.text
    assert "testclient" in page.text
    assert "someone.else" in page.text
    assert "someone.else" not in filtered.text


def test_only_admins_get_links_to_users(admin: SignedInClient, client: SignedInClient) -> None:
    new_user = {"username": "dee.dock", "display_name": "Dee Dock", "role": "clerk"}
    passwords = {"password": NEW_PHRASE, "confirm": NEW_PHRASE}
    user_page = admin.post("/admin/users", data={**new_user, **passwords}).headers["location"]
    link = f'href="{user_page.split("?")[0]}"'

    assert link in admin.get("/audit").text
    assert link not in client.get("/audit").text

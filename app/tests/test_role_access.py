"""Roles: clerks handle shipments up to picked, shipping needs a supervisor, users need an admin."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import MakeUser, SignedInClient
from larkspur_track.models import Shipment, User
from larkspur_track.roles import UserRole
from larkspur_track.workflow import ShipmentStatus, role_needed_for

SignInAs = Callable[[User], SignedInClient]
FORM = {
    "tracking_number": "LRK-ROLE-0001",
    "consignee": "Acme Retail (fictional)",
    "destination": "Dock 4, Greensboro NC",
}


@pytest.fixture
def clerk(make_user: MakeUser, sign_in_as: SignInAs) -> SignedInClient:
    return sign_in_as(make_user("cal.clerk", UserRole.CLERK))


@pytest.fixture
def admin(make_user: MakeUser, sign_in_as: SignInAs) -> SignedInClient:
    return sign_in_as(make_user("ada.admin", UserRole.ADMIN))


def _receive(client: SignedInClient) -> str:
    response = client.post("/shipments", data=FORM)
    assert response.status_code == 303
    return response.headers["location"]


def _advance(client: SignedInClient, location: str, target: str) -> int:
    return client.post(f"{location}/advance", data={"target": target}).status_code


@pytest.mark.parametrize(
    ("target", "role"),
    [
        (ShipmentStatus.STORED, UserRole.CLERK),
        (ShipmentStatus.PICKED, UserRole.CLERK),
        (ShipmentStatus.SHIPPED, UserRole.SUPERVISOR),
    ],
)
def test_role_needed_for_each_step(target: ShipmentStatus, role: UserRole) -> None:
    assert role_needed_for(target) is role


def test_clerk_handles_a_shipment_up_to_picked(clerk: SignedInClient) -> None:
    location = _receive(clerk)

    assert _advance(clerk, location, "stored") == 303
    assert _advance(clerk, location, "picked") == 303


def test_clerk_cannot_ship(clerk: SignedInClient, db_session: Session) -> None:
    location = _receive(clerk)
    _advance(clerk, location, "stored")
    _advance(clerk, location, "picked")

    page = clerk.get(location)
    response = clerk.post(f"{location}/advance", data={"target": "shipped"})

    assert "A supervisor marks this shipment as shipped." in page.text
    assert "Mark as shipped" not in page.text
    assert response.status_code == 403
    assert "needs the supervisor role" in response.text
    shipment = db_session.get(Shipment, location.rsplit("/", 1)[1])
    assert shipment is not None
    db_session.refresh(shipment)
    assert shipment.status is ShipmentStatus.PICKED


def test_supervisor_ships(client: SignedInClient) -> None:
    location = _receive(client)
    for target in ("stored", "picked"):
        _advance(client, location, target)

    assert "Mark as shipped" in client.get(location).text
    assert _advance(client, location, "shipped") == 303


@pytest.mark.parametrize("role", [UserRole.CLERK, UserRole.SUPERVISOR])
def test_only_admins_manage_users(
    make_user: MakeUser, sign_in_as: SignInAs, role: UserRole
) -> None:
    signed_in = sign_in_as(make_user("not.admin", role))

    page = signed_in.get("/shipments")
    response = signed_in.get("/admin/users")
    change = signed_in.post("/admin/users", data={"username": "new.user"})

    assert 'href="/admin/users"' not in page.text
    assert response.status_code == 403
    assert "needs the admin role" in response.text
    assert change.status_code == 403


def test_admin_sees_the_users_page(admin: SignedInClient) -> None:
    assert 'href="/admin/users"' in admin.get("/shipments").text
    assert admin.get("/admin/users").status_code == 200


def test_anonymous_visitor_is_sent_to_sign_in(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/admin/users")

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in?next=%2Fadmin%2Fusers"


def test_admin_changes_need_the_csrf_token(admin: SignedInClient) -> None:
    response = admin.post("/admin/users", data={"username": "new.user"}, with_csrf=False)

    assert response.status_code == 403
    assert "Nothing was changed" in response.text

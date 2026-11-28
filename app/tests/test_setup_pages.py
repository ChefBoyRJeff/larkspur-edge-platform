"""The Setup page, and the customer, bin, dock door and carrier fields on the shipment pages."""

from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import TEST_CUSTOMER, MakeUser, SignedInClient
from larkspur_track.audit import AuditAction
from larkspur_track.models import AuditEvent, Customer, User
from larkspur_track.roles import UserRole

SignInAs = Callable[[User], SignedInClient]
FORM = {
    "tracking_number": "LRK-SETUP-0001",
    "consignee": "Acme Retail (fictional)",
    "destination": "Greensboro NC",
}


def _receive(client: SignedInClient) -> str:
    response = client.post("/shipments", data=FORM)
    assert response.status_code == 303
    return response.headers["location"]


def test_clerks_cannot_open_setup(make_user: MakeUser, sign_in_as: SignInAs) -> None:
    clerk = sign_in_as(make_user("cal.clerk", UserRole.CLERK))

    assert clerk.get("/setup").status_code == 403
    assert clerk.post("/setup/customers", data={"name": "Sneaky Co"}).status_code == 403
    assert "/setup" not in clerk.get("/shipments").text


def test_supervisor_adds_a_customer_and_it_is_audited(
    client: SignedInClient, db_session: Session
) -> None:
    response = client.post("/setup/customers", data={"name": "Blue Ridge Foods"})

    assert response.status_code == 303
    assert "Blue Ridge Foods" in client.get("/setup").text
    assert "Blue Ridge Foods" in client.get("/shipments/new").text
    entry = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CUSTOMER_ADDED)
    ).one()
    assert entry.detail == "Blue Ridge Foods"


def test_setup_shows_input_errors(client: SignedInClient) -> None:
    response = client.post("/setup/locations", data={"code": "A 01", "kind": "bin"})

    assert response.status_code == 422
    assert "letters, numbers or dashes" in response.text


def test_deactivated_customer_is_no_longer_offered(
    client: SignedInClient, db_session: Session
) -> None:
    customer = db_session.scalars(select(Customer).where(Customer.name == TEST_CUSTOMER)).one()

    response = client.post(f"/setup/customer/{customer.id}/active", data={"active": "false"})

    assert response.status_code == 303
    assert TEST_CUSTOMER not in client.get("/shipments/new").text


def test_receiving_needs_a_customer(client: SignedInClient) -> None:
    response = client.post("/shipments", data=FORM, with_defaults=False)

    assert response.status_code == 422
    assert "Choose a customer." in response.text


@pytest.mark.parametrize(
    ("target", "missing", "message"),
    [("stored", "bin_id", "Choose a bin."), ("picked", "carrier_id", "Choose a carrier.")],
)
def test_steps_need_their_details(
    client: SignedInClient, target: str, missing: str, message: str
) -> None:
    location = _receive(client)
    if target == "picked":
        client.post(f"{location}/advance", data={"target": "stored"})

    response = client.post(f"{location}/advance", data={"target": target, missing: ""})

    assert response.status_code == 422
    assert message in response.text


def test_detail_page_shows_customer_bin_and_carrier(client: SignedInClient) -> None:
    location = _receive(client)
    client.post(f"{location}/advance", data={"target": "stored"})
    client.post(f"{location}/advance", data={"target": "picked"})

    page = client.get(location).text

    assert TEST_CUSTOMER in page
    assert "Bin A-01-1" in page
    assert "Dock door D1, carrier Test Freight (fictional)" in page


def test_search_finds_a_shipment_by_bin(client: SignedInClient) -> None:
    location = _receive(client)
    client.post(f"{location}/advance", data={"target": "stored"})

    assert "LRK-SETUP-0001" in client.get("/shipments", params={"q": "a-01-1"}).text
    assert "LRK-SETUP-0001" not in client.get("/shipments", params={"q": "B-02-2"}).text

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from larkspur_track.models import Shipment

FORM = {
    "tracking_number": "LRK-TEST-0100",
    "consignee": "Acme Retail (fictional)",
    "destination": "Dock 4, Greensboro NC",
    "note": "",
}


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Shipment)) or 0


def _receive(client: TestClient, **changes: str) -> str:
    response = client.post("/shipments", data={**FORM, **changes})
    assert response.status_code == 303
    return response.headers["location"]


def test_home_redirects_to_the_shipment_list(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/shipments"


def test_receive_then_detail_shows_the_next_step(client: TestClient) -> None:
    location = _receive(client, tracking_number=" lrk-test-0100 ")

    page = client.get(location)

    assert page.status_code == 200
    assert "LRK-TEST-0100" in page.text
    assert "Mark as stored" in page.text


def test_invalid_form_is_rejected_and_nothing_is_saved(
    client: TestClient, db_session: Session
) -> None:
    response = client.post("/shipments", data={**FORM, "tracking_number": "x", "consignee": " "})

    assert response.status_code == 422
    assert "4 to 40 letters" in response.text
    assert "Enter who the shipment is for" in response.text
    assert _count(db_session) == 0


def test_duplicate_tracking_number_shows_an_error(client: TestClient, db_session: Session) -> None:
    _receive(client)

    response = client.post("/shipments", data=FORM)

    assert response.status_code == 409
    assert "already exists" in response.text
    assert _count(db_session) == 1


def test_advance_moves_on_and_adds_to_the_timeline(client: TestClient) -> None:
    location = _receive(client)

    response = client.post(f"{location}/advance", data={"target": "stored", "note": "Bay 12"})
    page = client.get(location)

    assert response.status_code == 303
    assert "Mark as picked" in page.text
    assert "received → stored" in page.text
    assert "Bay 12" in page.text


def test_out_of_date_advance_is_refused(client: TestClient) -> None:
    location = _receive(client)

    response = client.post(f"{location}/advance", data={"target": "picked"})

    assert response.status_code == 409
    assert "out of date" in response.text
    assert "Mark as stored" in response.text


def test_unknown_shipment_returns_404(client: TestClient) -> None:
    assert client.get(f"/shipments/{uuid.uuid4()}").status_code == 404


def test_user_input_is_escaped(client: TestClient) -> None:
    location = _receive(client, consignee="<script>alert(1)</script>")

    page = client.get(location)

    assert "&lt;script&gt;" in page.text
    assert "<script>alert(1)</script>" not in page.text


def test_list_shows_received_shipments(client: TestClient) -> None:
    _receive(client)

    page = client.get("/shipments")

    assert page.status_code == 200
    assert "LRK-TEST-0100" in page.text

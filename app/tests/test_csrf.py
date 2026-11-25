"""Changes need the session's CSRF token and must come from this site."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import BASE_URL, MakeUser, SignedInClient
from larkspur_track.models import Shipment
from larkspur_track.sessions import start_session

FORM = {
    "tracking_number": "LRK-CSRF-0001",
    "consignee": "Acme Retail (fictional)",
    "destination": "Dock 4, Greensboro NC",
}
FORGED = "not-the-sessions-token"


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Shipment)) or 0


def test_forms_carry_the_sessions_token(client: SignedInClient) -> None:
    page = client.get("/shipments/new")

    assert f'name="csrf_token" value="{client.csrf_token}"' in page.text


def test_change_with_the_token_is_accepted(client: SignedInClient, db_session: Session) -> None:
    response = client.post(
        "/shipments", data=FORM, headers={"Sec-Fetch-Site": "same-origin", "Origin": BASE_URL}
    )

    assert response.status_code == 303
    assert _count(db_session) == 1


def test_change_without_a_token_is_refused(client: SignedInClient, db_session: Session) -> None:
    response = client.post("/shipments", data=FORM, with_csrf=False)

    assert response.status_code == 403
    assert "Nothing was changed" in response.text
    assert _count(db_session) == 0


def test_change_with_a_wrong_token_is_refused(client: SignedInClient, db_session: Session) -> None:
    response = client.post("/shipments", data={**FORM, "csrf_token": FORGED})

    assert response.status_code == 403
    assert _count(db_session) == 0


def test_another_sessions_token_is_refused(
    client: SignedInClient, db_session: Session, make_user: MakeUser
) -> None:
    _, other = start_session(db_session, make_user("cal.clerk"), datetime.now(UTC))

    response = client.post("/shipments", data={**FORM, "csrf_token": other.csrf_token})

    assert response.status_code == 403
    assert _count(db_session) == 0


def test_cross_site_request_is_refused_even_with_the_token(
    client: SignedInClient, db_session: Session
) -> None:
    response = client.post("/shipments", data=FORM, headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403
    assert _count(db_session) == 0


def test_other_origin_is_refused_even_with_the_token(
    client: SignedInClient, db_session: Session
) -> None:
    response = client.post("/shipments", data=FORM, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert _count(db_session) == 0


def test_sign_out_needs_the_token(client: SignedInClient) -> None:
    response = client.post("/sign-out", with_csrf=False)

    assert response.status_code == 403
    assert client.get("/shipments").status_code == 200

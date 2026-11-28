"""The shipment list's filters, sorting, pages and CSV export."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from conftest import SignedInClient
from larkspur_track.catalog import add_customer
from larkspur_track.listing import parse_list_request, shipments_csv
from larkspur_track.models import Shipment, ShipmentEvent
from larkspur_track.queries import (
    ShipmentFilter,
    count_shipments,
    search_shipments,
)
from larkspur_track.services import create_shipment

ZONE = "America/New_York"


def _add(session: Session, number: str, received: datetime, customer_id=None) -> Shipment:  # noqa: ANN001
    shipment = create_shipment(
        session,
        tracking_number=number,
        consignee="Acme Retail (fictional)",
        destination="Greensboro NC",
        customer_id=customer_id,
    )
    shipment.created_at = received
    session.execute(
        update(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == shipment.id)
        .values(occurred_at=received)
    )
    session.flush()
    return shipment


def _numbers(shipments: list[Shipment]) -> list[str]:
    return [shipment.tracking_number for shipment in shipments]


def _list(**values: str):  # noqa: ANN202
    base = dict(q="", status="", customer="", received_from="", received_to="", sort="", page="1")
    return parse_list_request(**{**base, **values}, zone=ZONE)


def test_date_range_uses_local_days_and_includes_the_last_day(db_session: Session) -> None:
    # 11-10 23:30 New York is 11-11 04:30 UTC; it belongs to 11-10 locally.
    _add(db_session, "LRK-D-1", datetime(2026, 11, 11, 4, 30, tzinfo=UTC))
    _add(db_session, "LRK-D-2", datetime(2026, 11, 12, 15, 0, tzinfo=UTC))
    chosen = _list(received_from="2026-11-10", received_to="2026-11-10")

    assert _numbers(search_shipments(db_session, chosen.filters)) == ["LRK-D-1"]
    assert chosen.params == {"from": "2026-11-10", "to": "2026-11-10", "sort": "newest"}


def test_customer_filter_sorts_and_pages(db_session: Session) -> None:
    blue = add_customer(db_session, "Blue Ridge Foods (fictional)")
    start = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
    for number in range(3):
        _add(db_session, f"LRK-C-{number}", start + timedelta(hours=number), blue.id)
    _add(db_session, "LRK-OTHER", start)
    only_blue = ShipmentFilter(customer_id=blue.id)

    assert count_shipments(db_session, only_blue) == 3
    assert _numbers(search_shipments(db_session, only_blue, sort="oldest", limit=2)) == [
        "LRK-C-0",
        "LRK-C-1",
    ]
    assert _numbers(search_shipments(db_session, only_blue, limit=2, offset=2)) == ["LRK-C-0"]


def test_bad_values_are_ignored(db_session: Session) -> None:
    chosen = _list(status="lost", customer="nope", received_from="11/10", sort="drop", page="-3")

    assert chosen.filters == ShipmentFilter()
    assert (chosen.sort, chosen.page) == ("newest", 1)


def test_csv_neutralises_formulas(db_session: Session) -> None:
    shipment = _add(db_session, "LRK-CSV-1", datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    shipment.consignee = '=HYPERLINK("http://example.com")'

    lines = shipments_csv([shipment], ZONE).splitlines()

    assert lines[0].startswith("tracking_number,customer,consignee")
    assert "'=HYPERLINK" in lines[1]
    assert lines[1].endswith("2026-11-02T10:00:00-05:00")


def test_list_page_and_csv_download(client: SignedInClient) -> None:
    for number in range(27):
        client.post(
            "/shipments",
            data={
                "tracking_number": f"LRK-P-{number:03d}",
                "consignee": "Acme",
                "destination": "NC",
            },
        )

    first = client.get("/shipments").text
    second = client.get("/shipments", params={"page": "2"}).text
    export = client.get("/shipments.csv", params={"q": "LRK-P"})

    assert "Showing 1–25 of 27" in first and "Page 1 of 2" in first
    # One transaction gives every row the same received time; ties sort by tracking number.
    assert "LRK-P-026" in second and "LRK-P-000" not in second
    assert export.headers["content-type"].startswith("text/csv")
    assert len(export.text.splitlines()) == 28

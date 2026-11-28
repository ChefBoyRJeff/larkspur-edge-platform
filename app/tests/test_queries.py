from sqlalchemy.orm import Session

from larkspur_track.models import Shipment
from larkspur_track.queries import count_by_status, search_shipments
from larkspur_track.services import StepDetails, advance_shipment, create_shipment
from larkspur_track.workflow import ShipmentStatus


def _add(session: Session, tracking_number: str, consignee: str) -> Shipment:
    return create_shipment(
        session, tracking_number=tracking_number, consignee=consignee, destination="Dock 4"
    )


def _tracking_numbers(shipments: list[Shipment]) -> set[str]:
    return {shipment.tracking_number for shipment in shipments}


def test_search_ignores_case_on_tracking_number_and_consignee(db_session: Session) -> None:
    _add(db_session, "LRK-A-0001", "Acme Retail")
    _add(db_session, "LRK-B-0002", "Blue Ridge Foods")

    assert _tracking_numbers(search_shipments(db_session, query="lrk-a")) == {"LRK-A-0001"}
    assert _tracking_numbers(search_shipments(db_session, query="blue ridge")) == {"LRK-B-0002"}
    assert _tracking_numbers(search_shipments(db_session)) == {"LRK-A-0001", "LRK-B-0002"}


def test_search_treats_like_wildcards_as_plain_text(db_session: Session) -> None:
    _add(db_session, "LRK-A-0001", "Acme Retail")

    assert search_shipments(db_session, query="%") == []
    assert search_shipments(db_session, query="_") == []


def test_search_filters_by_status(db_session: Session, step_details: StepDetails) -> None:
    first = _add(db_session, "LRK-A-0001", "Acme Retail")
    _add(db_session, "LRK-B-0002", "Blue Ridge Foods")

    advance_shipment(db_session, first.id, ShipmentStatus.STORED, details=step_details)

    stored = search_shipments(db_session, status=ShipmentStatus.STORED)
    assert _tracking_numbers(stored) == {"LRK-A-0001"}


def test_search_respects_the_limit(db_session: Session) -> None:
    for number in range(3):
        _add(db_session, f"LRK-L-000{number}", "Acme Retail")

    assert len(search_shipments(db_session, limit=2)) == 2


def test_count_by_status_includes_statuses_with_none(
    db_session: Session, step_details: StepDetails
) -> None:
    first = _add(db_session, "LRK-A-0001", "Acme Retail")
    _add(db_session, "LRK-B-0002", "Blue Ridge Foods")
    advance_shipment(db_session, first.id, ShipmentStatus.STORED, details=step_details)

    assert count_by_status(db_session) == {
        ShipmentStatus.RECEIVED: 1,
        ShipmentStatus.STORED: 1,
        ShipmentStatus.PICKED: 0,
        ShipmentStatus.SHIPPED: 0,
    }

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from larkspur_track.models import Shipment
from larkspur_track.services import ShipmentNotFoundError, advance_shipment, create_shipment
from larkspur_track.workflow import InvalidTransitionError, ShipmentStatus


def _new_shipment(session: Session, tracking_number: str = "LRK-TEST-0001") -> Shipment:
    return create_shipment(
        session,
        tracking_number=tracking_number,
        consignee="Acme Retail (fictional)",
        destination="Dock 4, Greensboro NC",
    )


def test_create_shipment_starts_received_with_one_event(db_session: Session) -> None:
    shipment = _new_shipment(db_session)

    assert isinstance(shipment.id, uuid.UUID)
    assert shipment.status is ShipmentStatus.RECEIVED
    assert [(e.from_status, e.to_status) for e in shipment.events] == [
        (None, ShipmentStatus.RECEIVED)
    ]


def test_full_workflow_records_every_step_in_order(db_session: Session) -> None:
    shipment = _new_shipment(db_session)

    for target in (ShipmentStatus.STORED, ShipmentStatus.PICKED, ShipmentStatus.SHIPPED):
        advance_shipment(db_session, shipment.id, target)
    db_session.expire_all()
    reloaded = db_session.get(Shipment, shipment.id)

    assert reloaded is not None
    assert reloaded.status is ShipmentStatus.SHIPPED
    assert [e.to_status for e in reloaded.events] == list(ShipmentStatus)


def test_invalid_move_is_rejected_and_changes_nothing(db_session: Session) -> None:
    shipment = _new_shipment(db_session)

    with pytest.raises(InvalidTransitionError):
        advance_shipment(db_session, shipment.id, ShipmentStatus.PICKED)
    db_session.expire_all()
    reloaded = db_session.get(Shipment, shipment.id)

    assert reloaded is not None
    assert reloaded.status is ShipmentStatus.RECEIVED
    assert len(reloaded.events) == 1


def test_unknown_shipment_is_reported(db_session: Session) -> None:
    with pytest.raises(ShipmentNotFoundError):
        advance_shipment(db_session, uuid.uuid4(), ShipmentStatus.STORED)


def test_duplicate_tracking_number_is_rejected(db_session: Session) -> None:
    _new_shipment(db_session, "LRK-TEST-0002")

    with pytest.raises(IntegrityError):
        _new_shipment(db_session, "LRK-TEST-0002")


def test_database_rejects_an_unknown_status(db_session: Session) -> None:
    """The CHECK constraint protects the data even if something bypasses the app."""
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO shipments (id, tracking_number, consignee, destination, status) "
                "VALUES (:id, 'LRK-TEST-0003', 'Test', 'Test', 'lost')"
            ),
            {"id": uuid.uuid4()},
        )

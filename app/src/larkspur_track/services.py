"""Shipment operations: every status change goes through the workflow and adds a timeline event.

Functions flush but never commit; the caller (a page or a job) owns the transaction.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from larkspur_track.models import Shipment, ShipmentEvent
from larkspur_track.workflow import ShipmentStatus, check_transition


class ShipmentNotFoundError(LookupError):
    """Raised when no shipment has the given id."""


def create_shipment(
    session: Session,
    *,
    tracking_number: str,
    consignee: str,
    destination: str,
    note: str | None = None,
) -> Shipment:
    """Register a received shipment and record the first timeline event."""
    shipment = Shipment(
        tracking_number=tracking_number,
        consignee=consignee,
        destination=destination,
        status=ShipmentStatus.RECEIVED,
    )
    shipment.events.append(
        ShipmentEvent(from_status=None, to_status=ShipmentStatus.RECEIVED, note=note)
    )
    session.add(shipment)
    session.flush()
    return shipment


def advance_shipment(
    session: Session,
    shipment_id: uuid.UUID,
    target: ShipmentStatus,
    note: str | None = None,
) -> ShipmentEvent:
    """Move a shipment to its next status. The row is locked so two clerks cannot both move it."""
    statement = (
        select(Shipment)
        .where(Shipment.id == shipment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    shipment = session.execute(statement).scalar_one_or_none()
    if shipment is None:
        raise ShipmentNotFoundError(str(shipment_id))
    check_transition(shipment.status, target)
    event = ShipmentEvent(from_status=shipment.status, to_status=target, note=note)
    shipment.events.append(event)
    shipment.status = target
    session.flush()
    return event

"""Shipment operations: every status change goes through the workflow and adds a timeline event,
which is also queued in the outbox for Azure (larkspur_track.outbox).

Functions flush but never commit; the caller (a page or a job) owns the transaction.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from larkspur_track.catalog import unassigned_customer
from larkspur_track.models import Carrier, Customer, Location, LocationKind, Shipment, ShipmentEvent
from larkspur_track.outbox import enqueue
from larkspur_track.workflow import ShipmentStatus, check_transition


class ShipmentNotFoundError(LookupError):
    """Raised when no shipment has the given id."""


class StepInputError(ValueError):
    """A step is missing what it must record (a customer, a bin, a dock door or a carrier)."""


@dataclass(frozen=True)
class StepDetails:
    """What a step records: storing needs a bin; picking needs a dock door and a carrier."""

    bin_id: uuid.UUID | None = None
    dock_door_id: uuid.UUID | None = None
    carrier_id: uuid.UUID | None = None


def _location(session: Session, location_id: uuid.UUID | None, kind: LocationKind) -> Location:
    location = None if location_id is None else session.get(Location, location_id)
    if location is None or location.kind is not kind or not location.is_active:
        raise StepInputError("Choose a bin." if kind is LocationKind.BIN else "Choose a dock door.")
    return location


def _carrier(session: Session, carrier_id: uuid.UUID | None) -> Carrier:
    carrier = None if carrier_id is None else session.get(Carrier, carrier_id)
    if carrier is None or not carrier.is_active:
        raise StepInputError("Choose a carrier.")
    return carrier


def _customer(session: Session, customer_id: uuid.UUID | None) -> Customer:
    if customer_id is None:
        return unassigned_customer(session)
    customer = session.get(Customer, customer_id)
    if customer is None or not customer.is_active:
        raise StepInputError("Choose a customer.")
    return customer


def create_shipment(
    session: Session,
    *,
    tracking_number: str,
    consignee: str,
    destination: str,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
) -> Shipment:
    """Register a received shipment and record the first timeline event.

    Without a customer the shipment belongs to Unassigned; the receive page always sends one.
    """
    shipment = Shipment(
        tracking_number=tracking_number,
        consignee=consignee,
        destination=destination,
        status=ShipmentStatus.RECEIVED,
        customer=_customer(session, customer_id),
    )
    shipment.events.append(
        ShipmentEvent(
            from_status=None, to_status=ShipmentStatus.RECEIVED, note=note, actor_user_id=actor_id
        )
    )
    session.add(shipment)
    session.flush()
    enqueue(session, shipment, shipment.events[0])
    return shipment


def advance_shipment(
    session: Session,
    shipment_id: uuid.UUID,
    target: ShipmentStatus,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
    details: StepDetails | None = None,
) -> ShipmentEvent:
    """Move a shipment to its next status. The row is locked so two clerks cannot both move it.

    Storing records a bin; picking records a dock door and a carrier (StepInputError if missing).
    """
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
    given = details or StepDetails()
    detail = None
    if target is ShipmentStatus.STORED:
        shipment.bin = _location(session, given.bin_id, LocationKind.BIN)
        detail = f"Bin {shipment.bin.code}"
    elif target is ShipmentStatus.PICKED:
        door = _location(session, given.dock_door_id, LocationKind.DOCK_DOOR)
        carrier = _carrier(session, given.carrier_id)
        shipment.dock_door, shipment.carrier = door, carrier
        detail = f"Dock door {door.code}, carrier {carrier.name}"
    event = ShipmentEvent(
        from_status=shipment.status,
        to_status=target,
        note=note,
        detail=detail,
        actor_user_id=actor_id,
    )
    shipment.events.append(event)
    shipment.status = target
    session.flush()
    enqueue(session, shipment, event)
    return event

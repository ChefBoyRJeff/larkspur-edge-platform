"""The sync outbox: every shipment change also queues an event for Azure (ADR-0009).

enqueue() runs inside the change's own transaction, so the change and its event are saved
together or not at all; the sync worker (larkspur_track.sync) sends them later, in order.
"""

from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session

from larkspur_track.models import OutboxEvent, Shipment, ShipmentEvent, User

RECEIVED = "shipment.received"
STATUS_CHANGED = "shipment.status_changed"


def event_payload(shipment: Shipment, event: ShipmentEvent, actor: str | None) -> dict[str, Any]:
    """The event as Azure receives it.

    Events queued by the backfill in migration 9b2e7d4c1a05 have the same shape without the
    customer, bin, dock_door, carrier and detail fields, which were added later (receivers treat
    a missing field as unknown).
    """
    return {
        "event_id": str(event.id),
        "event_type": RECEIVED if event.from_status is None else STATUS_CHANGED,
        "occurred_at": event.occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "shipment": {
            "id": str(shipment.id),
            "tracking_number": shipment.tracking_number,
            "consignee": shipment.consignee,
            "destination": shipment.destination,
            "customer": shipment.customer.name,
            "bin": shipment.bin.code if shipment.bin else None,
            "dock_door": shipment.dock_door.code if shipment.dock_door else None,
            "carrier": shipment.carrier.name if shipment.carrier else None,
        },
        "from_status": None if event.from_status is None else event.from_status.value,
        "to_status": event.to_status.value,
        "note": event.note,
        "detail": event.detail,
        "actor": actor,
    }


def enqueue(session: Session, shipment: Shipment, event: ShipmentEvent) -> OutboxEvent:
    """Queue one shipment event. The event must be flushed (it needs its id and time)."""
    session.refresh(event, ["occurred_at"])
    actor = None
    if event.actor_user_id is not None:
        user = session.get(User, event.actor_user_id)
        actor = None if user is None else user.username
    payload = event_payload(shipment, event, actor)
    row = OutboxEvent(
        event_id=event.id, event_type=payload["event_type"], payload=payload, attempts=0
    )
    session.add(row)
    session.flush()
    return row

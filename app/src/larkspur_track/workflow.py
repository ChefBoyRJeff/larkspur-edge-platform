"""Shipment status workflow: received -> stored -> picked -> shipped, one step at a time."""

from enum import StrEnum


class ShipmentStatus(StrEnum):
    """Where a shipment is in the warehouse."""

    RECEIVED = "received"
    STORED = "stored"
    PICKED = "picked"
    SHIPPED = "shipped"


ALLOWED_TRANSITIONS: dict[ShipmentStatus, ShipmentStatus] = {
    ShipmentStatus.RECEIVED: ShipmentStatus.STORED,
    ShipmentStatus.STORED: ShipmentStatus.PICKED,
    ShipmentStatus.PICKED: ShipmentStatus.SHIPPED,
}


class InvalidTransitionError(ValueError):
    """Raised when a status change is not allowed by the workflow."""

    def __init__(self, current: ShipmentStatus, target: ShipmentStatus) -> None:
        super().__init__(f"cannot move a shipment from {current} to {target}")
        self.current = current
        self.target = target


def next_status(current: ShipmentStatus) -> ShipmentStatus | None:
    """The one status a shipment can move to next, or None when it has shipped."""
    return ALLOWED_TRANSITIONS.get(current)


def check_transition(current: ShipmentStatus, target: ShipmentStatus) -> None:
    """Raise InvalidTransitionError unless target is the next status after current."""
    if ALLOWED_TRANSITIONS.get(current) != target:
        raise InvalidTransitionError(current, target)

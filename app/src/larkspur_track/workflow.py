"""Shipment status workflow: received -> stored -> picked -> shipped, one step at a time."""

from enum import StrEnum

from larkspur_track.roles import UserRole


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


# Shipping hands the goods to a carrier, so it needs a supervisor. Clerks do every earlier step.
_ROLE_NEEDED = {ShipmentStatus.SHIPPED: UserRole.SUPERVISOR}


def role_needed_for(target: ShipmentStatus) -> UserRole:
    """The lowest role that may move a shipment to this status."""
    return _ROLE_NEEDED.get(target, UserRole.CLERK)


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

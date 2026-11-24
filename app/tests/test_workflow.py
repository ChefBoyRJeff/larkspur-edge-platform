import itertools

import pytest

from larkspur_track.workflow import (
    InvalidTransitionError,
    ShipmentStatus,
    check_transition,
    next_status,
)

ALLOWED = {
    (ShipmentStatus.RECEIVED, ShipmentStatus.STORED),
    (ShipmentStatus.STORED, ShipmentStatus.PICKED),
    (ShipmentStatus.PICKED, ShipmentStatus.SHIPPED),
}
REJECTED = sorted(set(itertools.product(ShipmentStatus, repeat=2)) - ALLOWED)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_moves(current: ShipmentStatus, target: ShipmentStatus) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(("current", "target"), REJECTED)
def test_rejected_moves(current: ShipmentStatus, target: ShipmentStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        check_transition(current, target)


def test_next_status_follows_the_workflow_and_ends_at_shipped() -> None:
    assert next_status(ShipmentStatus.RECEIVED) is ShipmentStatus.STORED
    assert next_status(ShipmentStatus.STORED) is ShipmentStatus.PICKED
    assert next_status(ShipmentStatus.PICKED) is ShipmentStatus.SHIPPED
    assert next_status(ShipmentStatus.SHIPPED) is None

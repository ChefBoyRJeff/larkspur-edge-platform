"""Customers, carriers and warehouse locations, and the steps that record them."""

import uuid
from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from larkspur_track.catalog import (
    CatalogInputError,
    add_carrier,
    add_customer,
    add_location,
    customers,
    locations,
    set_active,
    unassigned_customer,
)
from larkspur_track.models import Customer, Location, LocationKind, Shipment
from larkspur_track.services import (
    StepDetails,
    StepInputError,
    advance_shipment,
    create_shipment,
)
from larkspur_track.workflow import ShipmentStatus


def _shipment(session: Session, customer_id: uuid.UUID | None = None) -> Shipment:
    return create_shipment(
        session,
        tracking_number="LRK-CAT-0001",
        consignee="Acme Retail (fictional)",
        destination="Greensboro NC",
        customer_id=customer_id,
    )


def test_migration_creates_the_starting_layout_and_unassigned(db_session: Session) -> None:
    doors = locations(db_session, LocationKind.DOCK_DOOR)
    bins = locations(db_session, LocationKind.BIN)

    assert [door.code for door in doors] == ["D1", "D2", "D3", "D4"]
    assert len(bins) == 45
    assert bins[0].code == "A-01-1" and bins[-1].code == "C-05-3"
    assert unassigned_customer(db_session).is_active is False


def test_names_and_codes_are_cleaned_and_checked(db_session: Session) -> None:
    customer = add_customer(db_session, "  Blue   Ridge Foods  ")
    location = add_location(db_session, " a-06-1 ", LocationKind.BIN)

    assert customer.name == "Blue Ridge Foods"
    assert location.code == "A-06-1"
    with pytest.raises(CatalogInputError, match="at least 2"):
        add_carrier(db_session, " x ")
    with pytest.raises(CatalogInputError, match="letters, numbers or dashes"):
        add_location(db_session, "A 06", LocationKind.BIN)


def test_duplicates_are_refused_without_breaking_the_transaction(db_session: Session) -> None:
    add_carrier(db_session, "Piedmont Freight")

    with pytest.raises(CatalogInputError, match="already exists"):
        add_carrier(db_session, "Piedmont Freight")
    with pytest.raises(CatalogInputError, match="already exists"):
        add_location(db_session, "D1", LocationKind.DOCK_DOOR)
    assert add_carrier(db_session, "Tar Heel Trucking").name == "Tar Heel Trucking"


def test_deactivated_customers_are_not_offered_and_unassigned_stays_inactive(
    db_session: Session,
) -> None:
    customer = add_customer(db_session, "Old Mill Supply")
    set_active(db_session, Customer, customer.id, False)

    assert customer not in customers(db_session, active_only=True)
    with pytest.raises(CatalogInputError):
        set_active(db_session, Customer, unassigned_customer(db_session).id, True)
    assert set_active(db_session, Customer, uuid.uuid4(), True) is None


def test_new_shipment_needs_an_active_customer(db_session: Session) -> None:
    customer = add_customer(db_session, "Old Mill Supply")
    set_active(db_session, Customer, customer.id, False)

    with pytest.raises(StepInputError, match="customer"):
        _shipment(db_session, customer.id)
    assert _shipment(db_session).customer.name == "Unassigned"


def test_storing_needs_an_active_bin_and_records_it(
    db_session: Session, step_details: StepDetails
) -> None:
    shipment = _shipment(db_session)
    door = StepDetails(bin_id=step_details.dock_door_id)

    with pytest.raises(StepInputError, match="bin"):
        advance_shipment(db_session, shipment.id, ShipmentStatus.STORED)
    with pytest.raises(StepInputError, match="bin"):
        advance_shipment(db_session, shipment.id, ShipmentStatus.STORED, details=door)
    event = advance_shipment(db_session, shipment.id, ShipmentStatus.STORED, details=step_details)

    assert shipment.bin is not None and shipment.bin.code == "A-01-1"
    assert event.detail == "Bin A-01-1"


def test_picking_needs_a_dock_door_and_a_carrier(
    db_session: Session, step_details: StepDetails
) -> None:
    shipment = _shipment(db_session)
    advance_shipment(db_session, shipment.id, ShipmentStatus.STORED, details=step_details)
    no_carrier = StepDetails(dock_door_id=step_details.dock_door_id)

    with pytest.raises(StepInputError, match="carrier"):
        advance_shipment(db_session, shipment.id, ShipmentStatus.PICKED, details=no_carrier)
    assert shipment.status is ShipmentStatus.STORED and shipment.dock_door is None
    event = advance_shipment(db_session, shipment.id, ShipmentStatus.PICKED, details=step_details)

    assert event.detail == "Dock door D1, carrier Test Freight (fictional)"
    assert shipment.carrier is not None


def test_inactive_bin_is_refused(db_session: Session, step_details: StepDetails) -> None:
    shipment = _shipment(db_session)
    set_active(db_session, Location, step_details.bin_id, False)

    with pytest.raises(StepInputError, match="bin"):
        advance_shipment(db_session, shipment.id, ShipmentStatus.STORED, details=step_details)


def test_migration_gives_existing_shipments_the_unassigned_customer(
    use_database: Callable[[str], None], test_database_url: str, alembic_config: Config
) -> None:
    use_database(test_database_url)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "9b2e7d4c1a05")
    engine = create_engine(test_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO shipments (id, tracking_number, consignee, destination, status) "
                    "VALUES (gen_random_uuid(), 'LRK-OLD-0002', 'Old Co', 'Dock 1', 'received')"
                )
            )
        command.upgrade(alembic_config, "head")
        with Session(engine) as session:
            shipment = session.scalars(
                select(Shipment).where(Shipment.tracking_number == "LRK-OLD-0002")
            ).one()
            assert shipment.customer.name == "Unassigned"
            assert session.scalar(select(func.count()).select_from(Location)) == 49
    finally:
        engine.dispose()
        command.downgrade(alembic_config, "base")
        command.upgrade(alembic_config, "head")

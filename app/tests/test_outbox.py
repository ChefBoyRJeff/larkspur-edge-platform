from collections.abc import Callable

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from conftest import MakeUser
from larkspur_track.models import OutboxEvent, Shipment, ShipmentEvent
from larkspur_track.outbox import RECEIVED, STATUS_CHANGED, event_payload
from larkspur_track.roles import UserRole
from larkspur_track.services import StepDetails, advance_shipment, create_shipment
from larkspur_track.workflow import ShipmentStatus


def _outbox(session: Session) -> list[OutboxEvent]:
    return list(session.scalars(select(OutboxEvent).order_by(OutboxEvent.id)))


def _new(session: Session, tracking_number: str = "LRK-OUT-0001", **kwargs: object) -> Shipment:
    return create_shipment(
        session,
        tracking_number=tracking_number,
        consignee="Acme Retail (fictional)",
        destination="Dock 4, Greensboro NC",
        **kwargs,
    )


def test_receiving_a_shipment_queues_one_event(db_session: Session) -> None:
    shipment = _new(db_session, note="pallet 3 of 4")

    [row] = _outbox(db_session)
    event = shipment.events[0]
    assert row.event_id == event.id
    assert row.event_type == RECEIVED
    assert row.sent_at is None and row.attempts == 0
    assert row.payload == {
        "event_id": str(event.id),
        "event_type": RECEIVED,
        "occurred_at": row.payload["occurred_at"],
        "shipment": {
            "id": str(shipment.id),
            "tracking_number": "LRK-OUT-0001",
            "consignee": "Acme Retail (fictional)",
            "destination": "Dock 4, Greensboro NC",
            "customer": "Unassigned",
            "bin": None,
            "dock_door": None,
            "carrier": None,
        },
        "from_status": None,
        "to_status": "received",
        "note": "pallet 3 of 4",
        "detail": None,
        "actor": None,
    }
    assert row.payload["occurred_at"].endswith("Z")


def test_each_status_change_queues_an_event_in_order(
    db_session: Session, make_user: MakeUser, step_details: StepDetails
) -> None:
    clerk = make_user("cal.clerk", UserRole.CLERK)
    shipment = _new(db_session, actor_id=clerk.id)
    advance_shipment(
        db_session, shipment.id, ShipmentStatus.STORED, actor_id=clerk.id, details=step_details
    )
    advance_shipment(
        db_session, shipment.id, ShipmentStatus.PICKED, note="aisle 7", details=step_details
    )

    rows = _outbox(db_session)
    assert [(r.event_type, r.payload["from_status"], r.payload["to_status"]) for r in rows] == [
        (RECEIVED, None, "received"),
        (STATUS_CHANGED, "received", "stored"),
        (STATUS_CHANGED, "stored", "picked"),
    ]
    assert [r.payload["actor"] for r in rows] == ["cal.clerk", "cal.clerk", None]
    assert rows[2].payload["note"] == "aisle 7"
    assert len({r.event_id for r in rows}) == 3


def test_a_change_and_its_event_are_saved_together(db_session: Session) -> None:
    savepoint = db_session.begin_nested()
    _new(db_session, "LRK-OUT-0002")
    savepoint.rollback()

    assert _outbox(db_session) == []
    assert (
        db_session.scalar(select(Shipment).where(Shipment.tracking_number == "LRK-OUT-0002"))
        is None
    )


def test_migration_queues_events_recorded_before_the_outbox(
    use_database: Callable[[str], None], test_database_url: str, alembic_config: Config
) -> None:
    use_database(test_database_url)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "c4e3ce4e1078")
    engine = create_engine(test_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO shipments (id, tracking_number, consignee, destination, status) "
                    "VALUES ('11111111-1111-4111-8111-111111111111', 'LRK-OLD-0001', 'Old Co', "
                    "'Dock 1', 'stored')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO shipment_events (id, shipment_id, from_status, to_status, note, "
                    "occurred_at) VALUES "
                    "('22222222-2222-4222-8222-222222222222', "
                    "'11111111-1111-4111-8111-111111111111', "
                    "NULL, 'received', NULL, '2026-11-20 10:00:00.123456+00'), "
                    "('33333333-3333-4333-8333-333333333333', "
                    "'11111111-1111-4111-8111-111111111111', "
                    "'received', 'stored', 'shelf B', '2026-11-20 11:30:00+00')"
                )
            )
        command.upgrade(alembic_config, "head")
        with Session(engine) as session:
            rows = _outbox(session)
            shipment = session.get(Shipment, rows[0].payload["shipment"]["id"])
            events = list(
                session.scalars(select(ShipmentEvent).order_by(ShipmentEvent.occurred_at))
            )
            assert [r.event_id for r in rows] == [e.id for e in events]
            # The backfill builds the same payload as the application does, without the fields
            # added after it (customer, locations, carrier, detail).
            expected = [event_payload(shipment, e, None) for e in events]
            for payload in expected:
                payload.pop("detail")
                for field in ("customer", "bin", "dock_door", "carrier"):
                    payload["shipment"].pop(field)
            assert [r.payload for r in rows] == expected
            assert rows[0].payload["occurred_at"] == "2026-11-20T10:00:00.123456Z"
            assert [r.sent_at for r in rows] == [None, None]
    finally:
        engine.dispose()
        command.downgrade(alembic_config, "base")
        command.upgrade(alembic_config, "head")

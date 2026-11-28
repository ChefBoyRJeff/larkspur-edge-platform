"""Sample warehouse history for dev and prod: test users, customers, carriers, and shipments
spread over the past weeks, built with the same services the pages use.

Every shipment gets a tracking number starting LRK-S, which is how a second run is refused.
The sample history is not sent to Azure: its outbox rows are removed in the same transaction,
because the receiver should only see real activity.
"""

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from larkspur_track.audit import AuditAction, record
from larkspur_track.catalog import carriers, customers, locations
from larkspur_track.models import (
    Carrier,
    Customer,
    LocationKind,
    OutboxEvent,
    Shipment,
    ShipmentEvent,
    User,
)
from larkspur_track.roles import UserRole
from larkspur_track.services import StepDetails, advance_shipment, create_shipment
from larkspur_track.users import UserInputError, create_user
from larkspur_track.workflow import ShipmentStatus

PREFIX = "LRK-S"
CUSTOMERS = [
    "Blue Ridge Foods",
    "Piedmont Outdoor Supply",
    "Catawba Home Goods",
    "Tar River Pharmacy Supply",
    "Haw River Textiles",
    "Uwharrie Auto Parts",
]
CARRIERS = [
    "Piedmont Freight",
    "Tar Heel Trucking",
    "Yadkin Valley Express",
    "Coastal Plain Haulers",
]
CONSIGNEES = [
    "Greenway Market",
    "Summit Labs",
    "Harbor Supply Co.",
    "Crescent Hardware",
    "Pinecone Outfitters",
    "Riverbend Clinic",
    "Old Salem Bakery",
    "Lakeview Schools",
]
DESTINATIONS = [
    "Charlotte NC",
    "Greensboro NC",
    "Raleigh NC",
    "Durham NC",
    "Winston-Salem NC",
    "Asheville NC",
    "Wilmington NC",
    "Fayetteville NC",
]


class SeedError(RuntimeError):
    """The sample data cannot be added; nothing was changed."""


@dataclass(frozen=True)
class NewUser:
    username: str
    display_name: str
    role: UserRole
    password: str


@dataclass(frozen=True)
class SeedSummary:
    users: list[tuple[str, str]]
    customers: int
    carriers: int
    shipments: dict[ShipmentStatus, int]


def _named(session: Session, model: type[Customer] | type[Carrier], names: list[str]) -> list:
    existing = {row.name: row for row in session.scalars(select(model))}
    rows = []
    for name in names:
        row = existing.get(name) or model(name=name, is_active=True)
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


OPEN, CLOSE = 7, 17  # The dock works 07:00 to 17:00 local, Monday to Saturday.


def _open_hours(when: datetime, rng: random.Random, zone: ZoneInfo) -> datetime:
    """`when` if the dock is open then, otherwise early in the next working morning."""
    local = when.astimezone(zone)
    if OPEN <= local.hour < CLOSE and local.weekday() != 6:
        return local
    day = local.date() + timedelta(days=0 if local.hour < OPEN else 1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    start = datetime.combine(day, time(OPEN), tzinfo=zone)
    return start + timedelta(minutes=rng.randrange(0, 120))


def _received_time(now: datetime, days: int, rng: random.Random, zone: ZoneInfo) -> datetime:
    """A random working-hours time within the last `days` days."""
    for _ in range(10_000):
        day = (now.astimezone(zone) - timedelta(days=rng.uniform(0, days))).date()
        minutes = rng.randrange(OPEN * 60, CLOSE * 60)
        when = datetime.combine(day, time(), tzinfo=zone) + timedelta(minutes=minutes)
        if day.weekday() != 6 and when <= now:
            return when
    raise SeedError("The dock was not open in that period; choose more days. Nothing was added.")


def _storage_days(rng: random.Random) -> float:
    """Most goods leave within days; about one in ten sits for weeks (slow movers)."""
    return rng.uniform(15, 40) if rng.random() < 0.1 else rng.uniform(0.5, 9)


def seed(
    session: Session,
    new_users: list[NewUser],
    *,
    days: int,
    shipments: int,
    now: datetime,
    zone: str,
    rng: random.Random,
) -> SeedSummary:
    """Add the sample data. Flushes but never commits; the caller owns the transaction."""
    if session.scalar(select(Shipment.id).where(Shipment.tracking_number.startswith(PREFIX))):
        raise SeedError(f"Sample data is already here (shipments {PREFIX}...). Nothing was added.")
    tz = ZoneInfo(zone)
    created: list[User] = []
    for spec in new_users:
        try:
            created.append(
                create_user(
                    session,
                    username=spec.username,
                    display_name=spec.display_name,
                    role=spec.role,
                    password=spec.password,
                )
            )
        except UserInputError as error:
            raise SeedError(f"{spec.username}: {error} Nothing was added.") from None
        user = created[-1]
        detail = f"{user.username} as {user.role} (sample data)"
        record(
            session, AuditAction.USER_CREATED, target_type="user", target_id=user.id, detail=detail
        )
    clerks = [u for u in created if u.role is UserRole.CLERK] or created
    supervisors = [u for u in created if u.role is not UserRole.CLERK] or created
    customer_rows = _named(session, Customer, CUSTOMERS)
    carrier_rows = _named(session, Carrier, CARRIERS)
    bins = locations(session, LocationKind.BIN, active_only=True)
    doors = locations(session, LocationKind.DOCK_DOOR, active_only=True)
    if not bins or not doors:
        raise SeedError("There are no active bins or dock doors. Nothing was added.")

    numbers = rng.sample(range(100_000, 1_000_000), shipments)
    received_times = sorted(_received_time(now, days, rng, tz) for _ in range(shipments))
    seeded_events: list[uuid.UUID] = []
    for number, received in zip(numbers, received_times, strict=True):
        shipment = create_shipment(
            session,
            tracking_number=f"{PREFIX}{number}",
            consignee=rng.choice(CONSIGNEES),
            destination=rng.choice(DESTINATIONS),
            customer_id=rng.choice(customer_rows).id,
            actor_id=rng.choice(clerks).id,
        )
        steps = [(ShipmentStatus.RECEIVED, received)]
        stored = _open_hours(received + timedelta(minutes=rng.randrange(20, 6 * 60)), rng, tz)
        picked = _open_hours(stored + timedelta(days=_storage_days(rng)), rng, tz)
        shipped = _open_hours(picked + timedelta(minutes=rng.randrange(60, 30 * 60)), rng, tz)
        for target, when in (
            (ShipmentStatus.STORED, stored),
            (ShipmentStatus.PICKED, picked),
            (ShipmentStatus.SHIPPED, shipped),
        ):
            if when > now:
                break
            details = StepDetails(
                bin_id=rng.choice(bins).id,
                dock_door_id=rng.choice(doors).id,
                carrier_id=rng.choice(carrier_rows).id,
            )
            actor = rng.choice(supervisors if target is ShipmentStatus.SHIPPED else clerks)
            advance_shipment(session, shipment.id, target, actor_id=actor.id, details=details)
            steps.append((target, when))
        # Move the timeline to the chosen times (the services stamp the current time).
        shipment.created_at = received
        session.flush()
        for target, when in steps:
            session.execute(
                update(ShipmentEvent)
                .where(ShipmentEvent.shipment_id == shipment.id, ShipmentEvent.to_status == target)
                .values(occurred_at=when)
            )
        seeded_events.extend(
            session.scalars(
                select(ShipmentEvent.id).where(ShipmentEvent.shipment_id == shipment.id)
            )
        )
    session.execute(delete(OutboxEvent).where(OutboxEvent.event_id.in_(seeded_events)))
    session.flush()

    counts = dict.fromkeys(ShipmentStatus, 0)
    for status in session.scalars(
        select(Shipment.status).where(Shipment.tracking_number.startswith(PREFIX))
    ):
        counts[status] += 1
    detail = f"{shipments} shipments over {days} days, {len(created)} users"
    record(session, AuditAction.SAMPLE_DATA_ADDED, target_type="seed", detail=detail)
    return SeedSummary(
        users=[(u.username, u.role.value) for u in created],
        customers=len(customers(session)),
        carriers=len(carriers(session)),
        shipments=counts,
    )

"""Read-only shipment queries for the shipment list, its CSV export and the dashboard."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from larkspur_track.models import Customer, Location, Shipment
from larkspur_track.workflow import ShipmentStatus

PAGE_SIZE = 25
EXPORT_LIMIT = 10_000
SORTS = {
    "newest": (Shipment.created_at.desc(), Shipment.tracking_number),
    "oldest": (Shipment.created_at, Shipment.tracking_number),
    "tracking": (Shipment.tracking_number,),
    "customer": (Customer.name, Shipment.created_at.desc()),
    "status": (Shipment.status, Shipment.created_at.desc()),
}


@dataclass(frozen=True)
class ShipmentFilter:
    """What the shipment list shows. Received times are bounds in UTC: from inclusive, before
    exclusive (the page turns the chosen local days into these)."""

    query: str = ""
    status: ShipmentStatus | None = None
    customer_id: uuid.UUID | None = None
    received_from: datetime | None = None
    received_before: datetime | None = None


def _filtered(filters: ShipmentFilter) -> Select[tuple[Shipment]]:
    statement = (
        select(Shipment)
        .join(Customer, Shipment.customer_id == Customer.id)
        .outerjoin(Location, Shipment.bin_id == Location.id)
    )
    text = filters.query.strip()
    if text:
        # Ignores case; % and _ in the text match literally, not as wildcards.
        statement = statement.where(
            or_(
                Shipment.tracking_number.icontains(text, autoescape=True),
                Shipment.consignee.icontains(text, autoescape=True),
                Location.code.icontains(text, autoescape=True),
            )
        )
    if filters.status is not None:
        statement = statement.where(Shipment.status == filters.status)
    if filters.customer_id is not None:
        statement = statement.where(Shipment.customer_id == filters.customer_id)
    if filters.received_from is not None:
        statement = statement.where(Shipment.created_at >= filters.received_from)
    if filters.received_before is not None:
        statement = statement.where(Shipment.created_at < filters.received_before)
    return statement


def search_shipments(
    session: Session,
    filters: ShipmentFilter | None = None,
    *,
    sort: str = "newest",
    limit: int = PAGE_SIZE,
    offset: int = 0,
) -> list[Shipment]:
    """One page of matching shipments; an unknown sort falls back to newest first."""
    order = SORTS.get(sort, SORTS["newest"])
    statement = _filtered(filters or ShipmentFilter()).order_by(*order)
    return list(session.scalars(statement.limit(limit).offset(offset)))


def count_shipments(session: Session, filters: ShipmentFilter | None = None) -> int:
    subquery = _filtered(filters or ShipmentFilter()).subquery()
    return session.scalar(select(func.count()).select_from(subquery)) or 0


def export_shipments(
    session: Session, filters: ShipmentFilter | None = None, *, sort: str = "newest"
) -> Iterator[Shipment]:
    """Every matching shipment for the CSV export, up to EXPORT_LIMIT rows."""
    yield from search_shipments(session, filters, sort=sort, limit=EXPORT_LIMIT)


def count_by_status(session: Session) -> dict[ShipmentStatus, int]:
    """Number of shipments in each status, including statuses with none."""
    counts = dict.fromkeys(ShipmentStatus, 0)
    rows = session.execute(select(Shipment.status, func.count()).group_by(Shipment.status))
    for status, count in rows:
        counts[status] = count
    return counts

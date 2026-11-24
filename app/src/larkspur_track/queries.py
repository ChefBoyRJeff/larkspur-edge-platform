"""Read-only shipment queries for search and the dashboard."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from larkspur_track.models import Shipment
from larkspur_track.workflow import ShipmentStatus

SEARCH_LIMIT = 50


def search_shipments(
    session: Session,
    *,
    query: str = "",
    status: ShipmentStatus | None = None,
    limit: int = SEARCH_LIMIT,
) -> list[Shipment]:
    """Newest shipments first, matching the text (tracking number or consignee) and status.

    The text match ignores case, and % or _ in the text are matched literally, not as wildcards.
    """
    statement = select(Shipment)
    text = query.strip()
    if text:
        statement = statement.where(
            or_(
                Shipment.tracking_number.icontains(text, autoescape=True),
                Shipment.consignee.icontains(text, autoescape=True),
            )
        )
    if status is not None:
        statement = statement.where(Shipment.status == status)
    newest_first = statement.order_by(Shipment.created_at.desc(), Shipment.tracking_number)
    return list(session.scalars(newest_first.limit(limit)))


def count_by_status(session: Session) -> dict[ShipmentStatus, int]:
    """Number of shipments in each status, including statuses with none."""
    counts = dict.fromkeys(ShipmentStatus, 0)
    rows = session.execute(select(Shipment.status, func.count()).group_by(Shipment.status))
    for status, count in rows:
        counts[status] = count
    return counts

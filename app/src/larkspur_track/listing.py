"""Turns the shipment list's query string into a filter, and writes the CSV export."""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from larkspur_track.forms import parse_uuid
from larkspur_track.models import Shipment
from larkspur_track.queries import SORTS, ShipmentFilter
from larkspur_track.workflow import ShipmentStatus

QUERY_MAX = 100
CSV_COLUMNS = [
    "tracking_number",
    "customer",
    "consignee",
    "destination",
    "status",
    "bin",
    "dock_door",
    "carrier",
    "received",
]


@dataclass(frozen=True)
class ListRequest:
    """The list's settings as chosen on the page, cleaned; `params` rebuilds its links."""

    filters: ShipmentFilter
    sort: str
    page: int
    params: dict[str, str] = field(default_factory=dict)


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def parse_list_request(
    *,
    q: str,
    status: str,
    customer: str,
    received_from: str,
    received_to: str,
    sort: str,
    page: str,
    zone: str,
) -> ListRequest:
    """Clean every value; anything unusable is ignored rather than shown as an error."""
    query = q.strip()[:QUERY_MAX]
    try:
        status_filter = ShipmentStatus(status) if status else None
    except ValueError:
        status_filter = None
    customer_id = parse_uuid(customer)
    tz = ZoneInfo(zone)
    first, last = _day(received_from), _day(received_to)
    start = datetime.combine(first, time(), tzinfo=tz) if first else None
    # The last day is included: the bound is the start of the day after it.
    end = datetime.combine(last + timedelta(days=1), time(), tzinfo=tz) if last else None
    sort_key = sort if sort in SORTS else "newest"
    try:
        page_number = max(1, int(page))
    except ValueError:
        page_number = 1
    params = {
        "q": query,
        "status": status_filter.value if status_filter else "",
        "customer": str(customer_id) if customer_id else "",
        "from": first.isoformat() if first else "",
        "to": last.isoformat() if last else "",
        "sort": sort_key,
    }
    filters = ShipmentFilter(
        query=query,
        status=status_filter,
        customer_id=customer_id,
        received_from=start,
        received_before=end,
    )
    return ListRequest(filters, sort_key, page_number, {k: v for k, v in params.items() if v})


def query_string(params: dict[str, str], **changes: str | int) -> str:
    merged = {**params, **{key: str(value) for key, value in changes.items()}}
    return urlencode({key: value for key, value in merged.items() if value})


def _cell(value: str) -> str:
    # A cell starting with = + - @ (or a tab or return) can run as a formula in a spreadsheet.
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def shipments_csv(shipments: list[Shipment], zone: str) -> str:
    tz = ZoneInfo(zone)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for shipment in shipments:
        row = [
            shipment.tracking_number,
            shipment.customer.name,
            shipment.consignee,
            shipment.destination,
            shipment.status.value,
            shipment.bin.code if shipment.bin else "",
            shipment.dock_door.code if shipment.dock_door else "",
            shipment.carrier.name if shipment.carrier else "",
            shipment.created_at.astimezone(tz).isoformat(timespec="seconds"),
        ]
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()

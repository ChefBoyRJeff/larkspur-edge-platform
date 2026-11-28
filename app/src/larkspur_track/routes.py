"""Server-rendered shipment pages. Changes use POST, then redirect, so a refresh never resubmits."""

import uuid
from datetime import datetime, time
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from larkspur_track.audit import AuditAction, record_by
from larkspur_track.auth import RoleRequired, SessionDep, SignedIn, require_signed_in
from larkspur_track.catalog import carriers, customers, locations
from larkspur_track.config import get_settings
from larkspur_track.forms import clean_note, parse_shipment_form, parse_uuid
from larkspur_track.listing import ListRequest, parse_list_request, query_string, shipments_csv
from larkspur_track.models import LocationKind, Shipment
from larkspur_track.queries import (
    PAGE_SIZE,
    activity_since,
    count_by_status,
    count_shipments,
    daily_flow,
    export_shipments,
    oldest_waiting,
    search_shipments,
)
from larkspur_track.roles import has_role
from larkspur_track.services import (
    ShipmentNotFoundError,
    StepDetails,
    StepInputError,
    advance_shipment,
    create_shipment,
)
from larkspur_track.templating import render
from larkspur_track.workflow import (
    InvalidTransitionError,
    ShipmentStatus,
    next_status,
    role_needed_for,
)

OUT_OF_DATE = (
    "This shipment had already moved on, so the page was out of date. "
    "It now shows the current status."
)

# Every shipment page needs a signed-in user; POSTs also need the CSRF token (see auth.py).
router = APIRouter(dependencies=[Depends(require_signed_in)])
FormText = Annotated[str, Form()]
AuthDep = Annotated[SignedIn, Depends(require_signed_in)]


def _detail(
    request: Request,
    session: Session,
    shipment_id: uuid.UUID,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    shipment = session.get(Shipment, shipment_id)
    if shipment is None:
        return render(request, "not_found.html", {}, 404)
    following = next_status(shipment.status)
    auth: SignedIn = request.state.auth
    context = {
        "shipment": shipment,
        "bins": locations(session, LocationKind.BIN, active_only=True)
        if following is ShipmentStatus.STORED
        else [],
        "dock_doors": locations(session, LocationKind.DOCK_DOOR, active_only=True)
        if following is ShipmentStatus.PICKED
        else [],
        "carriers": carriers(session, active_only=True)
        if following is ShipmentStatus.PICKED
        else [],
        "next_status": following,
        "role_needed": role_needed_for(following) if following else None,
        "may_advance": following is not None and has_role(auth.role, role_needed_for(following)),
        "error": error,
    }
    return render(request, "shipments/detail.html", context, status_code)


@router.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/shipments", status_code=303)


def _list_request(request: Request) -> ListRequest:
    values = request.query_params
    return parse_list_request(
        q=values.get("q", ""),
        status=values.get("status", ""),
        customer=values.get("customer", ""),
        received_from=values.get("from", ""),
        received_to=values.get("to", ""),
        sort=values.get("sort", ""),
        page=values.get("page", "1"),
        zone=get_settings().display_timezone,
    )


SORT_LABELS = {
    "newest": "Newest first",
    "oldest": "Oldest first",
    "tracking": "Tracking number",
    "customer": "Customer",
    "status": "Status",
}


@router.get("/shipments")
def list_shipments(request: Request, session: SessionDep) -> HTMLResponse:
    chosen = _list_request(request)
    total = count_shipments(session, chosen.filters)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(chosen.page, pages)
    shipments = search_shipments(
        session, chosen.filters, sort=chosen.sort, offset=(page - 1) * PAGE_SIZE
    )
    params = chosen.params
    context = {
        "shipments": shipments,
        "total": total,
        "page": page,
        "pages": pages,
        "first_row": (page - 1) * PAGE_SIZE + 1,
        "params": params,
        "filtered": any(key != "sort" for key in params),
        "statuses": list(ShipmentStatus),
        "customers": customers(session),
        "sorts": SORT_LABELS,
        "previous_link": query_string(params, page=page - 1) if page > 1 else "",
        "next_link": query_string(params, page=page + 1) if page < pages else "",
        "csv_link": query_string(params),
    }
    return render(request, "shipments/list.html", context)


@router.get("/shipments.csv")
def export_csv(request: Request, session: SessionDep) -> Response:
    chosen = _list_request(request)
    zone = get_settings().display_timezone
    body = shipments_csv(list(export_shipments(session, chosen.filters, sort=chosen.sort)), zone)
    stamp = datetime.now(ZoneInfo(zone)).strftime("%Y%m%d-%H%M")
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="shipments-{stamp}.csv"'},
    )


@router.get("/dashboard")
def dashboard(request: Request, session: SessionDep) -> HTMLResponse:
    zone = get_settings().display_timezone
    now = datetime.now(ZoneInfo(zone))
    counts = count_by_status(session)
    flow = daily_flow(session, zone, now.date())
    context = {
        "counts": counts,
        "total": sum(counts.values()),
        "flow": flow,
        "flow_max": max([1, *(max(day.received, day.shipped) for day in flow)]),
        "waiting": oldest_waiting(session),
        "today": activity_since(session, datetime.combine(now.date(), time(), tzinfo=now.tzinfo)),
        "now": now,
    }
    return render(request, "dashboard.html", context)


def _new_page(
    request: Request,
    session: Session,
    values: dict[str, str],
    errors: dict[str, str],
    status_code: int = 200,
) -> HTMLResponse:
    context = {
        "values": values,
        "errors": errors,
        "customers": customers(session, active_only=True),
    }
    return render(request, "shipments/new.html", context, status_code)


@router.get("/shipments/new")
def new_shipment(request: Request, session: SessionDep) -> HTMLResponse:
    return _new_page(request, session, {}, {})


@router.post("/shipments")
def receive_shipment(
    request: Request,
    session: SessionDep,
    auth: AuthDep,
    customer_id: FormText = "",
    tracking_number: FormText = "",
    consignee: FormText = "",
    destination: FormText = "",
    note: FormText = "",
) -> Response:
    values = {
        "customer_id": customer_id,
        "tracking_number": tracking_number,
        "consignee": consignee,
        "destination": destination,
        "note": note,
    }
    data, errors = parse_shipment_form(tracking_number, consignee, destination, note)
    customer = parse_uuid(customer_id)
    if customer is None:
        errors["customer_id"] = "Choose a customer."
    if data is None or customer is None:
        return _new_page(request, session, values, errors, 422)
    try:
        shipment = create_shipment(
            session,
            tracking_number=data.tracking_number,
            consignee=data.consignee,
            destination=data.destination,
            note=data.note,
            actor_id=auth.user_id,
            customer_id=customer,
        )
        shipment_id = shipment.id
        record_by(
            session,
            request,
            auth,
            AuditAction.SHIPMENT_RECEIVED,
            target_type="shipment",
            target_id=shipment_id,
            detail=shipment.tracking_number,
        )
        session.commit()
    except StepInputError as problem:
        session.rollback()
        return _new_page(request, session, values, {"customer_id": str(problem)}, 422)
    except IntegrityError:
        session.rollback()
        errors = {"tracking_number": "A shipment with this tracking number already exists."}
        return _new_page(request, session, values, errors, 409)
    return RedirectResponse(f"/shipments/{shipment_id}", status_code=303)


@router.get("/shipments/{shipment_id}")
def shipment_detail(request: Request, shipment_id: uuid.UUID, session: SessionDep) -> HTMLResponse:
    return _detail(request, session, shipment_id)


@router.post("/shipments/{shipment_id}/advance")
def advance(
    request: Request,
    shipment_id: uuid.UUID,
    session: SessionDep,
    auth: AuthDep,
    target: FormText = "",
    note: FormText = "",
    bin_id: FormText = "",
    dock_door_id: FormText = "",
    carrier_id: FormText = "",
) -> Response:
    note_clean, note_error = clean_note(note)
    if note_error:
        return _detail(request, session, shipment_id, error=note_error, status_code=422)
    try:
        target_status = ShipmentStatus(target)
    except ValueError:
        return _detail(request, session, shipment_id, error="Unknown status.", status_code=422)
    if not has_role(auth.role, role_needed_for(target_status)):
        raise RoleRequired(role_needed_for(target_status))
    details = StepDetails(
        bin_id=parse_uuid(bin_id),
        dock_door_id=parse_uuid(dock_door_id),
        carrier_id=parse_uuid(carrier_id),
    )
    try:
        event = advance_shipment(
            session, shipment_id, target_status, note_clean, actor_id=auth.user_id, details=details
        )
        record_by(
            session,
            request,
            auth,
            AuditAction.SHIPMENT_ADVANCED,
            target_type="shipment",
            target_id=shipment_id,
            detail=f"{event.shipment.tracking_number}: {event.from_status} → {event.to_status}",
        )
        session.commit()
    except ShipmentNotFoundError:
        session.rollback()
        return render(request, "not_found.html", {}, 404)
    except InvalidTransitionError:
        session.rollback()
        return _detail(request, session, shipment_id, error=OUT_OF_DATE, status_code=409)
    except StepInputError as problem:
        session.rollback()
        return _detail(request, session, shipment_id, error=str(problem), status_code=422)
    return RedirectResponse(f"/shipments/{shipment_id}", status_code=303)

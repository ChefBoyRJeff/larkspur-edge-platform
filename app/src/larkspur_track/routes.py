"""Server-rendered shipment pages. Changes use POST, then redirect, so a refresh never resubmits."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from larkspur_track.audit import AuditAction, record_by
from larkspur_track.auth import RoleRequired, SessionDep, SignedIn, require_signed_in
from larkspur_track.catalog import carriers, customers, locations
from larkspur_track.forms import clean_note, parse_shipment_form, parse_uuid
from larkspur_track.models import LocationKind, Shipment
from larkspur_track.queries import SEARCH_LIMIT, count_by_status, search_shipments
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


@router.get("/shipments")
def list_shipments(
    request: Request, session: SessionDep, q: str = "", status: str = ""
) -> HTMLResponse:
    query = q.strip()[:100]
    try:
        status_filter = ShipmentStatus(status) if status else None
    except ValueError:
        status_filter = None
    shipments = search_shipments(session, query=query, status=status_filter)
    context = {
        "shipments": shipments,
        "q": query,
        "status": status_filter,
        "statuses": list(ShipmentStatus),
        "limit": SEARCH_LIMIT,
    }
    return render(request, "shipments/list.html", context)


@router.get("/dashboard")
def dashboard(request: Request, session: SessionDep) -> HTMLResponse:
    counts = count_by_status(session)
    return render(request, "dashboard.html", {"counts": counts, "total": sum(counts.values())})


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

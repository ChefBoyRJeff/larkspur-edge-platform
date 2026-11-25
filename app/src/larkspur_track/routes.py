"""Server-rendered shipment pages. Changes use POST, then redirect, so a refresh never resubmits."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from larkspur_track.auth import SessionDep, require_signed_in
from larkspur_track.forms import clean_note, parse_shipment_form
from larkspur_track.models import Shipment
from larkspur_track.queries import SEARCH_LIMIT, count_by_status, search_shipments
from larkspur_track.services import ShipmentNotFoundError, advance_shipment, create_shipment
from larkspur_track.templating import render
from larkspur_track.workflow import InvalidTransitionError, ShipmentStatus, next_status

OUT_OF_DATE = (
    "This shipment had already moved on, so the page was out of date. "
    "It now shows the current status."
)

# Every shipment page needs a signed-in user; POSTs also need the CSRF token (see auth.py).
router = APIRouter(dependencies=[Depends(require_signed_in)])
FormText = Annotated[str, Form()]


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
    context = {"shipment": shipment, "next_status": next_status(shipment.status), "error": error}
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


@router.get("/shipments/new")
def new_shipment(request: Request) -> HTMLResponse:
    return render(request, "shipments/new.html", {"values": {}, "errors": {}})


@router.post("/shipments")
def receive_shipment(
    request: Request,
    session: SessionDep,
    tracking_number: FormText = "",
    consignee: FormText = "",
    destination: FormText = "",
    note: FormText = "",
) -> Response:
    values = {
        "tracking_number": tracking_number,
        "consignee": consignee,
        "destination": destination,
        "note": note,
    }
    data, errors = parse_shipment_form(tracking_number, consignee, destination, note)
    if data is None:
        return render(request, "shipments/new.html", {"values": values, "errors": errors}, 422)
    try:
        shipment = create_shipment(
            session,
            tracking_number=data.tracking_number,
            consignee=data.consignee,
            destination=data.destination,
            note=data.note,
        )
        shipment_id = shipment.id
        session.commit()
    except IntegrityError:
        session.rollback()
        errors = {"tracking_number": "A shipment with this tracking number already exists."}
        return render(request, "shipments/new.html", {"values": values, "errors": errors}, 409)
    return RedirectResponse(f"/shipments/{shipment_id}", status_code=303)


@router.get("/shipments/{shipment_id}")
def shipment_detail(request: Request, shipment_id: uuid.UUID, session: SessionDep) -> HTMLResponse:
    return _detail(request, session, shipment_id)


@router.post("/shipments/{shipment_id}/advance")
def advance(
    request: Request,
    shipment_id: uuid.UUID,
    session: SessionDep,
    target: FormText = "",
    note: FormText = "",
) -> Response:
    note_clean, note_error = clean_note(note)
    if note_error:
        return _detail(request, session, shipment_id, error=note_error, status_code=422)
    try:
        target_status = ShipmentStatus(target)
    except ValueError:
        return _detail(request, session, shipment_id, error="Unknown status.", status_code=422)
    try:
        advance_shipment(session, shipment_id, target_status, note_clean)
        session.commit()
    except ShipmentNotFoundError:
        session.rollback()
        return render(request, "not_found.html", {}, 404)
    except InvalidTransitionError:
        session.rollback()
        return _detail(request, session, shipment_id, error=OUT_OF_DATE, status_code=409)
    return RedirectResponse(f"/shipments/{shipment_id}", status_code=303)

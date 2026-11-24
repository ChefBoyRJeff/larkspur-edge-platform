"""Server-rendered shipment pages. Changes use POST, then redirect, so a refresh never resubmits."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from larkspur_track.config import get_settings
from larkspur_track.db import get_session
from larkspur_track.forms import clean_note, parse_shipment_form
from larkspur_track.models import Shipment
from larkspur_track.services import ShipmentNotFoundError, advance_shipment, create_shipment
from larkspur_track.workflow import InvalidTransitionError, ShipmentStatus, next_status

OUT_OF_DATE = (
    "This shipment had already moved on, so the page was out of date. "
    "It now shows the current status."
)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def local_time(value: datetime) -> str:
    """Show a stored (UTC) time in the site's time zone, e.g. 11-24-2026 12:35 PM EST."""
    zone = ZoneInfo(get_settings().display_timezone)
    return value.astimezone(zone).strftime("%m-%d-%Y %I:%M %p %Z")


templates.env.filters["local_time"] = local_time

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
FormText = Annotated[str, Form()]


def _render(
    request: Request, name: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


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
        return _render(request, "not_found.html", {}, 404)
    context = {"shipment": shipment, "next_status": next_status(shipment.status), "error": error}
    return _render(request, "shipments/detail.html", context, status_code)


@router.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/shipments", status_code=303)


@router.get("/shipments")
def list_shipments(request: Request, session: SessionDep) -> HTMLResponse:
    shipments = session.scalars(
        select(Shipment).order_by(Shipment.created_at.desc()).limit(50)
    ).all()
    return _render(request, "shipments/list.html", {"shipments": shipments})


@router.get("/shipments/new")
def new_shipment(request: Request) -> HTMLResponse:
    return _render(request, "shipments/new.html", {"values": {}, "errors": {}})


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
        return _render(request, "shipments/new.html", {"values": values, "errors": errors}, 422)
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
        return _render(request, "shipments/new.html", {"values": values, "errors": errors}, 409)
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
        return _render(request, "not_found.html", {}, 404)
    except InvalidTransitionError:
        session.rollback()
        return _detail(request, session, shipment_id, error=OUT_OF_DATE, status_code=409)
    return RedirectResponse(f"/shipments/{shipment_id}", status_code=303)

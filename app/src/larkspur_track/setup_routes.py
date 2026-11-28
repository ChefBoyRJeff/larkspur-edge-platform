"""The Setup page: customers, carriers, bins and dock doors. Supervisors and admins only."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from larkspur_track.audit import AuditAction, record_by
from larkspur_track.auth import SessionDep, SignedIn, require_role
from larkspur_track.catalog import (
    CatalogInputError,
    add_carrier,
    add_customer,
    add_location,
    carriers,
    customers,
    locations,
    set_active,
)
from larkspur_track.models import Carrier, Customer, Location, LocationKind
from larkspur_track.roles import UserRole
from larkspur_track.templating import render

require_supervisor = require_role(UserRole.SUPERVISOR)
router = APIRouter(prefix="/setup", dependencies=[Depends(require_supervisor)])
SupervisorDep = Annotated[SignedIn, Depends(require_supervisor)]
FormText = Annotated[str, Form()]

KINDS: dict[str, type[Customer] | type[Carrier] | type[Location]] = {
    "customer": Customer,
    "carrier": Carrier,
    "location": Location,
}
# Shown after a change; the redirect carries only the key, never free text.
DONE = {"added": "Added.", "deactivated": "Deactivated.", "reactivated": "Reactivated."}


def _page(
    request: Request,
    session: Session,
    *,
    done: str = "",
    errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {
        "customers": customers(session),
        "carriers": carriers(session),
        "bins": locations(session, LocationKind.BIN),
        "dock_doors": locations(session, LocationKind.DOCK_DOOR),
        "done": DONE.get(done),
        "errors": errors or {},
    }
    return render(request, "setup.html", context, status_code)


def _done(key: str, anchor: str) -> RedirectResponse:
    return RedirectResponse(f"/setup?done={key}#{anchor}", status_code=303)


@router.get("")
def setup_page(request: Request, session: SessionDep, done: str = "") -> HTMLResponse:
    return _page(request, session, done=done)


@router.post("/customers")
def new_customer(
    request: Request, session: SessionDep, auth: SupervisorDep, name: FormText = ""
) -> Response:
    try:
        row = add_customer(session, name)
    except CatalogInputError as problem:
        return _page(request, session, errors={"customer": str(problem)}, status_code=422)
    record_by(
        session,
        request,
        auth,
        AuditAction.CUSTOMER_ADDED,
        target_type="customer",
        target_id=row.id,
        detail=row.name,
    )
    session.commit()
    return _done("added", "customers")


@router.post("/carriers")
def new_carrier(
    request: Request, session: SessionDep, auth: SupervisorDep, name: FormText = ""
) -> Response:
    try:
        row = add_carrier(session, name)
    except CatalogInputError as problem:
        return _page(request, session, errors={"carrier": str(problem)}, status_code=422)
    record_by(
        session,
        request,
        auth,
        AuditAction.CARRIER_ADDED,
        target_type="carrier",
        target_id=row.id,
        detail=row.name,
    )
    session.commit()
    return _done("added", "carriers")


@router.post("/locations")
def new_location(
    request: Request,
    session: SessionDep,
    auth: SupervisorDep,
    code: FormText = "",
    kind: FormText = "",
) -> Response:
    try:
        location_kind = LocationKind(kind)
    except ValueError:
        return _page(
            request, session, errors={"location": "Choose bin or dock door."}, status_code=422
        )
    try:
        row = add_location(session, code, location_kind)
    except CatalogInputError as problem:
        return _page(request, session, errors={"location": str(problem)}, status_code=422)
    record_by(
        session,
        request,
        auth,
        AuditAction.LOCATION_ADDED,
        target_type="location",
        target_id=row.id,
        detail=f"{row.code} ({row.kind})",
    )
    session.commit()
    return _done("added", "locations")


@router.post("/{kind}/{row_id}/active")
def change_active(
    request: Request,
    kind: str,
    row_id: uuid.UUID,
    session: SessionDep,
    auth: SupervisorDep,
    active: FormText = "",
) -> Response:
    model = KINDS.get(kind)
    if model is None or active not in ("true", "false"):
        return render(request, "not_found.html", {}, 404)
    make_active = active == "true"
    try:
        row = set_active(session, model, row_id, make_active)
    except CatalogInputError as problem:
        return _page(request, session, errors={kind: str(problem)}, status_code=422)
    if row is None:
        return render(request, "not_found.html", {}, 404)
    label = row.code if isinstance(row, Location) else row.name
    action = AuditAction.SETUP_REACTIVATED if make_active else AuditAction.SETUP_DEACTIVATED
    record_by(session, request, auth, action, target_type=kind, target_id=row.id, detail=label)
    session.commit()
    anchor = "locations" if kind == "location" else f"{kind}s"
    return _done("reactivated" if make_active else "deactivated", anchor)

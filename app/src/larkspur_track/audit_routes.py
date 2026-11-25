"""The audit log page. Supervisors and admins can read it; nobody can change it."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from larkspur_track.audit import RECENT_LIMIT, AuditAction, recent_events
from larkspur_track.auth import SessionDep, require_role
from larkspur_track.roles import UserRole
from larkspur_track.templating import render

router = APIRouter(prefix="/audit", dependencies=[Depends(require_role(UserRole.SUPERVISOR))])


@router.get("")
def audit_page(request: Request, session: SessionDep, action: str = "") -> HTMLResponse:
    try:
        chosen = AuditAction(action) if action else None
    except ValueError:
        chosen = None
    context = {
        "events": recent_events(session, action=chosen),
        "actions": list(AuditAction),
        "action": chosen,
        "limit": RECENT_LIMIT,
    }
    return render(request, "audit.html", context)

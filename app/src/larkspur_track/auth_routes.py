"""The sign-in and sign-out pages, and the responses for the two sign-in exceptions."""

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from larkspur_track.audit import AuditAction, client_address, record, record_by
from larkspur_track.auth import (
    SIGN_IN_FAILED,
    CsrfRejected,
    CurrentAuth,
    RoleRequired,
    SessionDep,
    SignedIn,
    SignInRequired,
    authenticate,
    clear_session_cookie,
    is_same_origin,
    require_signed_in,
    safe_next,
    session_cookie_name,
    set_session_cookie,
)
from larkspur_track.sessions import end_session, start_session
from larkspur_track.templating import render
from larkspur_track.users import find_user, is_locked, normalize_username

router = APIRouter()
FormText = Annotated[str, Form()]


@router.get("/sign-in")
def sign_in_page(
    request: Request, auth: CurrentAuth, next_page: Annotated[str, Query(alias="next")] = ""
) -> Response:
    if auth is not None:
        return RedirectResponse(safe_next(next_page), status_code=303)
    context = {"next": safe_next(next_page), "username": "", "error": None}
    return render(request, "sign_in.html", context)


@router.post("/sign-in")
def sign_in(
    request: Request,
    session: SessionDep,
    username: FormText = "",
    password: FormText = "",
    next_page: Annotated[str, Form(alias="next")] = "",
) -> Response:
    # No session exists yet, so there is no CSRF token to check; the same-site check stops
    # another site from signing the browser in to an account of its choosing.
    if not is_same_origin(request):
        raise CsrfRejected
    target = safe_next(next_page)
    now = datetime.now(UTC)
    user = authenticate(session, username, password, now)
    if user is None:
        known = find_user(session, username)
        detail = None
        if known is not None and not known.is_active:
            detail = "account deactivated"
        elif known is not None and is_locked(known, now):
            detail = "account locked"
        record(
            session,
            AuditAction.SIGN_IN_FAILED,
            actor_id=known.id if known else None,
            actor_username=normalize_username(username) or None,
            detail=detail,
            client_ip=client_address(request),
        )
        session.commit()  # keep the failed-attempt count, any lockout and the audit entry
        context = {"next": target, "username": username.strip(), "error": SIGN_IN_FAILED}
        return render(request, "sign_in.html", context, 422)
    # A new token at every sign-in, so a token planted before sign-in is never reused.
    old_token = request.cookies.get(session_cookie_name())
    if old_token:
        end_session(session, old_token)
    token, _ = start_session(session, user, now)
    record(
        session,
        AuditAction.SIGNED_IN,
        actor_id=user.id,
        actor_username=user.username,
        client_ip=client_address(request),
    )
    session.commit()
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(response, token)
    return response


@router.post("/sign-out")
def sign_out(
    request: Request,
    session: SessionDep,
    auth: Annotated[SignedIn, Depends(require_signed_in)],
) -> Response:
    token = request.cookies.get(session_cookie_name())
    if token:
        end_session(session, token)
    record_by(session, request, auth, AuditAction.SIGNED_OUT)
    session.commit()
    response = RedirectResponse("/sign-in", status_code=303)
    clear_session_cookie(response)
    return response


async def send_to_sign_in(request: Request, exc: Exception) -> Response:
    """Handler for SignInRequired: a page view comes back to the same page after sign-in."""
    target = "/sign-in"
    if request.method in ("GET", "HEAD"):
        page = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        target += "?" + urlencode({"next": page})
    response = RedirectResponse(target, status_code=303)
    if session_cookie_name() in request.cookies:
        clear_session_cookie(response)  # the cookie's session has ended
    return response


async def refuse_change(request: Request, exc: Exception) -> HTMLResponse:
    """Handler for CsrfRejected: nothing was changed."""
    return render(request, "forbidden.html", {}, 403)


async def refuse_role(request: Request, exc: Exception) -> HTMLResponse:
    """Handler for RoleRequired: say which role the page or action needs."""
    required = exc.required if isinstance(exc, RoleRequired) else None
    return render(request, "no_access.html", {"required": required}, 403)


EXCEPTION_HANDLERS = {
    SignInRequired: send_to_sign_in,
    CsrfRejected: refuse_change,
    RoleRequired: refuse_role,
}

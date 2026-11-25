"""User administration pages. Every page and action needs the admin role."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from larkspur_track.audit import AuditAction, record_by
from larkspur_track.auth import SessionDep, SignedIn, require_admin
from larkspur_track.models import User
from larkspur_track.roles import UserRole
from larkspur_track.templating import render
from larkspur_track.users import (
    UserInputError,
    change_role,
    create_user,
    is_locked,
    list_users,
    reset_password,
    set_active,
    unlock,
)

router = APIRouter(prefix="/admin/users", dependencies=[Depends(require_admin)])
AdminDep = Annotated[SignedIn, Depends(require_admin)]
FormText = Annotated[str, Form()]

# Shown after a change; the redirect carries only the key, never free text.
DONE = {
    "created": "User created.",
    "role": "Role changed. It applies from the user's next page view.",
    "deactivated": "User deactivated and signed out everywhere.",
    "reactivated": "User reactivated.",
    "password": "Password reset. The user was signed out everywhere.",
    "unlocked": "User unlocked.",
}
PASSWORDS_DIFFER = "The two passwords are different."


def _role(value: str) -> UserRole | None:
    try:
        return UserRole(value)
    except ValueError:
        return None


def _user_page(
    request: Request,
    session: Session,
    user_id: uuid.UUID,
    *,
    error: str | None = None,
    done: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    user = session.get(User, user_id)
    if user is None:
        return render(request, "not_found.html", {"thing": "User"}, 404)
    auth: SignedIn = request.state.auth
    context: dict[str, Any] = {
        "user": user,
        "is_self": user.id == auth.user_id,
        "locked": is_locked(user, datetime.now(UTC)),
        "roles": list(UserRole),
        "error": error,
        "done": DONE.get(done),
    }
    return render(request, "admin/user_detail.html", context, status_code)


def _audit(
    session: Session, request: Request, auth: SignedIn, action: AuditAction, user: User, detail: str
) -> None:
    record_by(session, request, auth, action, target_type="user", target_id=user.id, detail=detail)


def _back(user_id: uuid.UUID, done: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/users/{user_id}?done={done}", status_code=303)


@router.get("")
def users_page(request: Request, session: SessionDep, done: str = "") -> HTMLResponse:
    now = datetime.now(UTC)
    rows = [(user, is_locked(user, now)) for user in list_users(session)]
    return render(request, "admin/users.html", {"rows": rows, "done": DONE.get(done)})


@router.get("/new")
def new_user_page(request: Request) -> HTMLResponse:
    context = {"values": {"role": UserRole.CLERK}, "roles": list(UserRole), "error": None}
    return render(request, "admin/user_new.html", context)


@router.post("")
def add_user(
    request: Request,
    session: SessionDep,
    auth: AdminDep,
    username: FormText = "",
    display_name: FormText = "",
    role: FormText = "",
    password: FormText = "",
    confirm: FormText = "",
) -> Response:
    values = {"username": username, "display_name": display_name, "role": role}
    chosen = _role(role)
    error = None
    if chosen is None:
        error = "Choose a role."
    elif password != confirm:
        error = PASSWORDS_DIFFER
    else:
        try:
            user = create_user(
                session,
                username=username,
                display_name=display_name,
                role=chosen,
                password=password,
            )
            user_id = user.id
            detail = f"{user.username} as {user.role}"
            _audit(session, request, auth, AuditAction.USER_CREATED, user, detail)
            session.commit()
            return _back(user_id, "created")
        except UserInputError as problem:
            session.rollback()
            error = str(problem)
    context = {"values": values, "roles": list(UserRole), "error": error}
    return render(request, "admin/user_new.html", context, 422)


@router.get("/{user_id}")
def user_page(
    request: Request, user_id: uuid.UUID, session: SessionDep, done: str = ""
) -> HTMLResponse:
    return _user_page(request, session, user_id, done=done)


@router.post("/{user_id}/role")
def update_role(
    request: Request, user_id: uuid.UUID, session: SessionDep, auth: AdminDep, role: FormText = ""
) -> Response:
    user = session.get(User, user_id)
    chosen = _role(role)
    if user is None or chosen is None:
        return _user_page(request, session, user_id, error="Choose a role.", status_code=422)
    previous = user.role
    try:
        change_role(session, user, chosen, by=auth.user_id)
    except UserInputError as problem:
        session.rollback()
        return _user_page(request, session, user_id, error=str(problem), status_code=422)
    detail = f"{user.username}: {previous} → {chosen}"
    _audit(session, request, auth, AuditAction.USER_ROLE_CHANGED, user, detail)
    session.commit()
    return _back(user_id, "role")


@router.post("/{user_id}/active")
def update_active(
    request: Request,
    user_id: uuid.UUID,
    session: SessionDep,
    auth: AdminDep,
    active: FormText = "",
) -> Response:
    user = session.get(User, user_id)
    if user is None or active not in ("true", "false"):
        return _user_page(request, session, user_id, error="Unknown change.", status_code=422)
    try:
        set_active(session, user, active == "true", by=auth.user_id)
    except UserInputError as problem:
        session.rollback()
        return _user_page(request, session, user_id, error=str(problem), status_code=422)
    action = AuditAction.USER_REACTIVATED if active == "true" else AuditAction.USER_DEACTIVATED
    _audit(session, request, auth, action, user, user.username)
    session.commit()
    return _back(user_id, "reactivated" if active == "true" else "deactivated")


@router.post("/{user_id}/password")
def update_password(
    request: Request,
    user_id: uuid.UUID,
    session: SessionDep,
    auth: AdminDep,
    password: FormText = "",
    confirm: FormText = "",
) -> Response:
    user = session.get(User, user_id)
    if user is None:
        return _user_page(request, session, user_id)
    if password != confirm:
        return _user_page(request, session, user_id, error=PASSWORDS_DIFFER, status_code=422)
    try:
        reset_password(session, user, password, datetime.now(UTC))
    except UserInputError as problem:
        session.rollback()
        return _user_page(request, session, user_id, error=str(problem), status_code=422)
    _audit(session, request, auth, AuditAction.USER_PASSWORD_RESET, user, user.username)
    session.commit()
    return _back(user_id, "password")


@router.post("/{user_id}/unlock")
def unlock_user(
    request: Request, user_id: uuid.UUID, session: SessionDep, auth: AdminDep
) -> Response:
    user = session.get(User, user_id)
    if user is None:
        return _user_page(request, session, user_id)
    unlock(session, user)
    _audit(session, request, auth, AuditAction.USER_UNLOCKED, user, user.username)
    session.commit()
    return _back(user_id, "unlocked")

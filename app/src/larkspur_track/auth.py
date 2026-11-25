"""Signing in, and the checks every protected page runs before its route does.

Pages need a valid session cookie. Requests that change something (POST) must also come from
this site (Sec-Fetch-Site and Origin headers) and carry the session's CSRF token, so another
site cannot submit a form on a signed-in user's behalf.
"""

import hmac
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from larkspur_track.config import get_settings
from larkspur_track.db import get_session
from larkspur_track.models import User
from larkspur_track.passwords import hash_password, needs_rehash, verify_password
from larkspur_track.roles import UserRole, has_role
from larkspur_track.sessions import SESSION_LIFETIME, resume_session
from larkspur_track.users import find_user

MAX_FAILED_SIGN_INS = 5
LOCKOUT = timedelta(minutes=15)
SIGN_IN_FAILED = "The username or password is incorrect."
DEFAULT_PAGE = "/shipments"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The __Host- prefix makes browsers refuse the cookie unless it is Secure, has Path=/ and no
# Domain, so no other site or subdomain can set or replace it. It needs HTTPS.
SECURE_COOKIE = "__Host-larkspur_session"
LOCAL_COOKIE = "larkspur_session"

SessionDep = Annotated[Session, Depends(get_session)]


class SignInRequired(Exception):
    """No valid session. The handler sends the browser to the sign-in page."""


class CsrfRejected(Exception):
    """A change request without the page's token, or sent from another site."""


class RoleRequired(Exception):
    """The signed-in user's role is too low for this page or action."""

    def __init__(self, required: UserRole) -> None:
        super().__init__(f"needs the {required} role")
        self.required = required


@dataclass(frozen=True)
class SignedIn:
    """Who is signed in, copied from the database once per request."""

    user_id: uuid.UUID
    username: str
    display_name: str
    role: UserRole
    csrf_token: str


@lru_cache
def _dummy_hash() -> str:
    """A hash of a random password, verified for unknown users so they take as long as real ones."""
    return hash_password(secrets.token_urlsafe(16))


def authenticate(session: Session, username: str, password: str, now: datetime) -> User | None:
    """The user when the password is right and the account may sign in; otherwise None.

    Every attempt runs one Argon2 verification, so the response time does not reveal whether
    the username exists or the account is locked. Five wrong passwords lock the account for
    15 minutes. The caller commits, so failed attempts are counted.
    """
    user = find_user(session, username)
    if user is None or not user.is_active:
        verify_password(_dummy_hash(), password)
        return None
    if user.locked_until is not None and user.locked_until > now:
        verify_password(user.password_hash, password)
        return None
    if not verify_password(user.password_hash, password):
        user.failed_sign_ins += 1
        if user.failed_sign_ins >= MAX_FAILED_SIGN_INS:
            user.failed_sign_ins = 0
            user.locked_until = now + LOCKOUT
        session.flush()
        return None
    user.failed_sign_ins = 0
    user.locked_until = None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    session.flush()
    return user


def session_cookie_name() -> str:
    return SECURE_COOKIE if get_settings().session_cookie_secure else LOCAL_COOKIE


def set_session_cookie(response: Response, token: str) -> None:
    """HttpOnly keeps scripts from reading it; SameSite=Lax keeps it off cross-site POSTs."""
    response.set_cookie(
        session_cookie_name(),
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        session_cookie_name(),
        path="/",
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def safe_next(target: str | None) -> str:
    """Where to go after signing in: only a path on this site, never another site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return DEFAULT_PAGE


def is_same_origin(request: Request) -> bool:
    """False when the browser says the request came from another site.

    Browsers send Sec-Fetch-Site on every request and Origin on every POST. A header that is
    missing (an older browser, or a test client) is not treated as a cross-site request; the
    CSRF token still has to match.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin")
    if origin is not None:
        return origin == f"{request.url.scheme}://{request.url.netloc}"
    return True


def current_auth(request: Request, session: SessionDep) -> SignedIn | None:
    """Who is signed in, from the session cookie. Ended sessions are removed."""
    request.state.auth = None
    token = request.cookies.get(session_cookie_name())
    if not token:
        return None
    row = resume_session(session, token, datetime.now(UTC))
    auth = None
    if row is not None:
        user = row.user
        auth = SignedIn(user.id, user.username, user.display_name, user.role, row.csrf_token)
    # Keep the last-seen time, or the removal of an ended session, even if the page fails.
    session.commit()
    request.state.auth = auth
    return auth


CurrentAuth = Annotated[SignedIn | None, Depends(current_auth)]


async def require_signed_in(request: Request, auth: CurrentAuth) -> SignedIn:
    """Router dependency: a signed-in user, and for POST the same-site and CSRF checks."""
    if auth is None:
        raise SignInRequired
    if request.method not in SAFE_METHODS:
        if not is_same_origin(request):
            raise CsrfRejected
        # FastAPI has already parsed the form for the route; Starlette returns the same copy.
        sent = (await request.form()).get("csrf_token")
        if not isinstance(sent, str) or not hmac.compare_digest(
            sent.encode(), auth.csrf_token.encode()
        ):
            raise CsrfRejected
    return auth


def require_role(required: UserRole) -> Callable[[SignedIn], Awaitable[SignedIn]]:
    """A dependency that allows the given role and every role above it."""

    async def check(auth: Annotated[SignedIn, Depends(require_signed_in)]) -> SignedIn:
        if not has_role(auth.role, required):
            raise RoleRequired(required)
        return auth

    return check


require_admin = require_role(UserRole.ADMIN)

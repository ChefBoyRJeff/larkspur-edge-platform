"""Jinja2 templates shared by every page, and the values every page can use."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from larkspur_track.config import get_settings
from larkspur_track.roles import UserRole, has_role


def _page_context(request: Request) -> dict[str, Any]:
    """The running release, who is signed in, their role, and the CSRF token forms must send."""
    auth = getattr(request.state, "auth", None)
    return {
        "app_release": get_settings().app_release,
        "current_user": auth,
        "csrf_token": auth.csrf_token if auth else "",
        "is_admin": auth is not None and has_role(auth.role, UserRole.ADMIN),
    }


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates", context_processors=[_page_context]
)


def local_time(value: datetime) -> str:
    """Show a stored (UTC) time in the site's time zone, e.g. 11-24-2026 12:35 PM EST."""
    zone = ZoneInfo(get_settings().display_timezone)
    return value.astimezone(zone).strftime("%m-%d-%Y %I:%M %p %Z")


templates.env.filters["local_time"] = local_time


def render(
    request: Request, name: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)

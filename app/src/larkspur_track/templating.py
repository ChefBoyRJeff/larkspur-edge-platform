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
    settings = get_settings()
    environment = settings.site_environment.strip().lower()
    return {
        "app_release": settings.app_release,
        "environment_badge": "" if environment == "prod" else environment.upper(),
        "current_user": auth,
        "csrf_token": auth.csrf_token if auth else "",
        "is_supervisor": auth is not None and has_role(auth.role, UserRole.SUPERVISOR),
        "is_admin": auth is not None and has_role(auth.role, UserRole.ADMIN),
    }


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates", context_processors=[_page_context]
)


def local_time(value: datetime) -> str:
    """Show a stored (UTC) time in the site's time zone, e.g. 11-24-2026 12:35 PM EST."""
    zone = ZoneInfo(get_settings().display_timezone)
    return value.astimezone(zone).strftime("%m-%d-%Y %I:%M %p %Z")


def age(value: datetime, now: datetime) -> str:
    """How long ago, roughly: 3 d 4 h, 5 h 10 min, or 12 min."""
    minutes = max(0, int((now - value).total_seconds() // 60))
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


templates.env.filters["local_time"] = local_time
templates.env.filters["age"] = age


def render(
    request: Request, name: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)

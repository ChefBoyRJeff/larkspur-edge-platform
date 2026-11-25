"""The audit log: who did what, when, from which address. Functions flush but never commit.

Entries are written in the same transaction as the change they describe, so a change is never
saved without its entry (or the other way round). Passwords and tokens are never recorded.
"""

import uuid
from enum import StrEnum

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from larkspur_track.auth import SignedIn
from larkspur_track.models import AuditEvent

RECENT_LIMIT = 200


class AuditAction(StrEnum):
    SIGNED_IN = "signed_in"
    SIGN_IN_FAILED = "sign_in_failed"
    SIGNED_OUT = "signed_out"
    SHIPMENT_RECEIVED = "shipment_received"
    SHIPMENT_ADVANCED = "shipment_advanced"
    USER_CREATED = "user_created"
    USER_ROLE_CHANGED = "user_role_changed"
    USER_DEACTIVATED = "user_deactivated"
    USER_REACTIVATED = "user_reactivated"
    USER_PASSWORD_RESET = "user_password_reset"  # noqa: S105 (an action name, not a password)
    USER_UNLOCKED = "user_unlocked"


def client_address(request: Request) -> str | None:
    """The client's address; uvicorn takes it from Traefik's X-Forwarded-For (ADR-0005)."""
    return request.client.host if request.client else None


def record(
    session: Session,
    action: AuditAction,
    *,
    actor_id: uuid.UUID | None = None,
    actor_username: str | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: str | None = None,
    client_ip: str | None = None,
) -> AuditEvent:
    entry = AuditEvent(
        action=action.value,
        actor_user_id=actor_id,
        actor_username=actor_username[:40] if actor_username else None,
        target_type=target_type,
        target_id=target_id,
        detail=detail[:500] if detail else None,
        client_ip=client_ip[:45] if client_ip else None,
    )
    session.add(entry)
    session.flush()
    return entry


def record_by(
    session: Session,
    request: Request,
    auth: SignedIn,
    action: AuditAction,
    *,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: str | None = None,
) -> AuditEvent:
    """Record an action by the signed-in user of this request."""
    return record(
        session,
        action,
        actor_id=auth.user_id,
        actor_username=auth.username,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        client_ip=client_address(request),
    )


def recent_events(
    session: Session, *, action: AuditAction | None = None, limit: int = RECENT_LIMIT
) -> list[AuditEvent]:
    """The newest entries first."""
    statement = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
    if action is not None:
        statement = statement.where(AuditEvent.action == action.value)
    return list(session.scalars(statement))

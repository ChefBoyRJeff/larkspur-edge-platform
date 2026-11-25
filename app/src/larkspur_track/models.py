"""ORM models: shipments, their append-only status timeline, users and their sessions."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from larkspur_track.db import Base
from larkspur_track.roles import UserRole
from larkspur_track.workflow import ShipmentStatus


def _text_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Stored as text with a CHECK constraint, so adding a value is a simple migration."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


def _status_type(name: str) -> Enum:
    return _text_enum(ShipmentStatus, name)


class Shipment(Base):
    """A shipment handled by the warehouse."""

    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tracking_number: Mapped[str] = mapped_column(String(40), unique=True)
    consignee: Mapped[str] = mapped_column(String(120))
    destination: Mapped[str] = mapped_column(String(200))
    status: Mapped[ShipmentStatus] = mapped_column(
        _status_type("status"), default=ShipmentStatus.RECEIVED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Quoted because ShipmentEvent is defined below; SQLAlchemy resolves it by name.
    events: Mapped[list["ShipmentEvent"]] = relationship(  # noqa: UP037
        back_populates="shipment", order_by="ShipmentEvent.occurred_at"
    )


class ShipmentEvent(Base):
    """One status change. Rows are only ever added, never edited or deleted."""

    __tablename__ = "shipment_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id"), index=True)
    from_status: Mapped[ShipmentStatus | None] = mapped_column(_status_type("from_status"))
    to_status: Mapped[ShipmentStatus] = mapped_column(_status_type("to_status"))
    note: Mapped[str | None] = mapped_column(String(500))
    # clock_timestamp(), not now(): now() is fixed for a whole transaction and would tie events.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    shipment: Mapped[Shipment] = relationship(back_populates="events")


class User(Base):
    """A person who signs in. Usernames are stored in lower case."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(_text_enum(UserRole, "role"))
    # Argon2id hash in PHC format ($argon2id$v=19$m=...,t=...,p=...$salt$hash), never the password.
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    failed_sign_ins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserSession(Base):
    """A signed-in browser. Its cookie holds a random token; only the token's SHA-256 is stored."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Every form on the session's pages must send this back (CSRF synchronizer token).
    csrf_token: Mapped[str] = mapped_column(String(64))
    # Set by the application from one clock, so expiry checks and tests agree.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()

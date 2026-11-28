"""ORM models: shipments and their timeline, the customers, carriers and warehouse locations they
refer to, users, sessions, the audit log, and the sync outbox."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class LocationKind(StrEnum):
    """Bins hold stored goods; dock doors are where picked goods wait for the carrier."""

    BIN = "bin"
    DOCK_DOOR = "dock_door"


# The customer every shipment received before customers existed belongs to (see the migration).
UNASSIGNED_CUSTOMER = "Unassigned"


class Customer(Base):
    """A company the warehouse handles goods for (Larkspur works as a third-party warehouse)."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Carrier(Base):
    """A trucking company that collects shipments from a dock door."""

    __tablename__ = "carriers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Location(Base):
    """A place in the warehouse: a bin (for example A-03-2: aisle, bay, level) or a dock door."""

    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    kind: Mapped[LocationKind] = mapped_column(_text_enum(LocationKind, "kind"))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    # Set when the shipment is stored, and when it is picked (dock door and carrier).
    bin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"), index=True)
    dock_door_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"))
    carrier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("carriers.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship()
    bin: Mapped[Location | None] = relationship(foreign_keys=[bin_id])
    dock_door: Mapped[Location | None] = relationship(foreign_keys=[dock_door_id])
    carrier: Mapped[Carrier | None] = relationship()
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
    # What the step recorded, for the timeline: "Bin A-03-2", "Dock door D2, carrier ...".
    detail: Mapped[str | None] = mapped_column(String(200))
    # clock_timestamp(), not now(): now() is fixed for a whole transaction and would tie events.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    # Who made the change. Empty for events recorded before sign-in existed.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    shipment: Mapped[Shipment] = relationship(back_populates="events")
    # Quoted because User is defined below.
    actor: Mapped["User | None"] = relationship()  # noqa: UP037


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


class AuditEvent(Base):
    """One security-relevant action. Rows are only ever added: a database trigger rejects
    UPDATE, DELETE and TRUNCATE on this table."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), index=True
    )
    action: Mapped[str] = mapped_column(String(40), index=True)
    # Empty when nobody was signed in, e.g. a failed sign-in with an unknown username.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    # The username at the time, or as typed for a failed sign-in.
    actor_username: Mapped[str | None] = mapped_column(String(40))
    target_type: Mapped[str | None] = mapped_column(String(20))
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    detail: Mapped[str | None] = mapped_column(String(500))
    # The client's address as Traefik saw it (see ADR-0005); IPv6 needs up to 45 characters.
    client_ip: Mapped[str | None] = mapped_column(String(45))


class OutboxEvent(Base):
    """A shipment change waiting to be sent to Azure (Integration module, ADR-0009).

    Written in the same transaction as the change, so a change and its event are saved together
    or not at all. The sync worker sends unsent rows in id order and stamps sent_at. Delivery is
    at least once: event_id (the shipment event's id) lets the receiver drop duplicates.
    """

    __tablename__ = "outbox"
    __table_args__ = (
        # The worker only ever looks for unsent rows, oldest first.
        Index("ix_outbox_unsent", "id", postgresql_where=text("sent_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    # The last send error, without credentials; cleared when the event is sent.
    last_error: Mapped[str | None] = mapped_column(String(300))

"""ORM models: shipments and their append-only status timeline."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from larkspur_track.db import Base
from larkspur_track.workflow import ShipmentStatus


def _status_type(name: str) -> Enum:
    """Status stored as text with a CHECK constraint, so adding a status is a simple migration."""
    return Enum(
        ShipmentStatus,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda statuses: [status.value for status in statuses],
        validate_strings=True,
    )


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

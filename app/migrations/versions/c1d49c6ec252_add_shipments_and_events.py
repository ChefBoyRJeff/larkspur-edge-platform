"""add shipments and events

Revision ID: c1d49c6ec252
Revises: c29c06e506a5
Create Date: 2026-11-24 12:25:31.731714
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d49c6ec252"
down_revision: str | Sequence[str] | None = "c29c06e506a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tracking_number", sa.String(length=40), nullable=False),
        sa.Column("consignee", sa.String(length=120), nullable=False),
        sa.Column("destination", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "received",
                "stored",
                "picked",
                "shipped",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        sa.UniqueConstraint("tracking_number", name=op.f("uq_shipments_tracking_number")),
    )
    op.create_table(
        "shipment_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column(
            "from_status",
            sa.Enum(
                "received",
                "stored",
                "picked",
                "shipped",
                name="from_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum(
                "received",
                "stored",
                "picked",
                "shipped",
                name="to_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipments.id"], name=op.f("fk_shipment_events_shipment_id_shipments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_events")),
    )
    op.create_index(
        op.f("ix_shipment_events_shipment_id"), "shipment_events", ["shipment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_shipment_events_shipment_id"), table_name="shipment_events")
    op.drop_table("shipment_events")
    op.drop_table("shipments")

"""add outbox

Revision ID: 9b2e7d4c1a05
Revises: c4e3ce4e1078
Create Date: 2026-11-25 13:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9b2e7d4c1a05"
down_revision: str | Sequence[str] | None = "c4e3ce4e1078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Queue every shipment event recorded before the outbox existed, oldest first, so Azure receives
# the full history. The payload matches larkspur_track.outbox.event_payload.
BACKFILL = """
INSERT INTO outbox (event_id, event_type, payload, created_at, attempts)
SELECT e.id,
       CASE WHEN e.from_status IS NULL THEN 'shipment.received' ELSE 'shipment.status_changed' END,
       jsonb_build_object(
           'event_id', e.id::text,
           'event_type',
           CASE WHEN e.from_status IS NULL THEN 'shipment.received'
                ELSE 'shipment.status_changed' END,
           'occurred_at',
           to_char(e.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
           'shipment', jsonb_build_object(
               'id', s.id::text,
               'tracking_number', s.tracking_number,
               'consignee', s.consignee,
               'destination', s.destination
           ),
           'from_status', e.from_status,
           'to_status', e.to_status,
           'note', e.note,
           'actor', u.username
       ),
       e.occurred_at,
       0
FROM shipment_events e
JOIN shipments s ON s.id = e.shipment_id
LEFT JOIN users u ON u.id = e.actor_user_id
ORDER BY e.occurred_at, e.id
"""


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
        sa.UniqueConstraint("event_id", name=op.f("uq_outbox_event_id")),
    )
    op.create_index(
        "ix_outbox_unsent",
        "outbox",
        ["id"],
        unique=False,
        postgresql_where=sa.text("sent_at IS NULL"),
    )
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_unsent", table_name="outbox", postgresql_where=sa.text("sent_at IS NULL")
    )
    op.drop_table("outbox")

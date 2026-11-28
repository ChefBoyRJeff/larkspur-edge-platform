"""add customers, carriers and warehouse locations

Revision ID: d7a3f1b2c845
Revises: 9b2e7d4c1a05
Create Date: 2026-11-28 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3f1b2c845"
down_revision: str | Sequence[str] | None = "9b2e7d4c1a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A starting layout: dock doors D1 to D4, and bins in aisles A to C, bays 01 to 05, levels 1 to 3
# (A-01-1 ... C-05-3). Supervisors add or retire locations on the Setup page.
DOCK_DOORS = [f"D{door}" for door in range(1, 5)]
BINS = [
    f"{aisle}-{bay:02d}-{level}" for aisle in "ABC" for bay in range(1, 6) for level in (1, 2, 3)
]


def _lookup_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
        sa.UniqueConstraint("name", name=op.f(f"uq_{name}_name")),
    )


def upgrade() -> None:
    _lookup_table("customers")
    _lookup_table("carriers")
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "bin",
                "dock_door",
                name="kind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
        sa.UniqueConstraint("code", name=op.f("uq_locations_code")),
    )
    rows = [{"code": code, "kind": "dock_door"} for code in DOCK_DOORS]
    rows += [{"code": code, "kind": "bin"} for code in BINS]
    op.get_bind().execute(
        sa.text(
            "INSERT INTO locations (id, code, kind, is_active) "
            "VALUES (gen_random_uuid(), :code, :kind, true)"
        ),
        rows,
    )

    # Shipments received before customers existed belong to "Unassigned", which is inactive so
    # it is never offered for new shipments.
    op.execute(
        "INSERT INTO customers (id, name, is_active) "
        "VALUES (gen_random_uuid(), 'Unassigned', false)"
    )
    op.add_column("shipments", sa.Column("customer_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE shipments SET customer_id = (SELECT id FROM customers WHERE name = 'Unassigned')"
    )
    op.alter_column("shipments", "customer_id", nullable=False)
    op.add_column("shipments", sa.Column("bin_id", sa.Uuid(), nullable=True))
    op.add_column("shipments", sa.Column("dock_door_id", sa.Uuid(), nullable=True))
    op.add_column("shipments", sa.Column("carrier_id", sa.Uuid(), nullable=True))
    for column, table in (
        ("customer_id", "customers"),
        ("bin_id", "locations"),
        ("dock_door_id", "locations"),
        ("carrier_id", "carriers"),
    ):
        op.create_foreign_key(
            op.f(f"fk_shipments_{column}_{table}"), "shipments", table, [column], ["id"]
        )
    op.create_index(op.f("ix_shipments_customer_id"), "shipments", ["customer_id"], unique=False)
    op.create_index(op.f("ix_shipments_bin_id"), "shipments", ["bin_id"], unique=False)
    op.add_column("shipment_events", sa.Column("detail", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("shipment_events", "detail")
    op.drop_index(op.f("ix_shipments_bin_id"), table_name="shipments")
    op.drop_index(op.f("ix_shipments_customer_id"), table_name="shipments")
    for column, table in (
        ("carrier_id", "carriers"),
        ("dock_door_id", "locations"),
        ("bin_id", "locations"),
        ("customer_id", "customers"),
    ):
        op.drop_constraint(op.f(f"fk_shipments_{column}_{table}"), "shipments", type_="foreignkey")
        op.drop_column("shipments", column)
    op.drop_table("locations")
    op.drop_table("carriers")
    op.drop_table("customers")

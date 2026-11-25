"""add audit log

Revision ID: c4e3ce4e1078
Revises: e65756b783e5
Create Date: 2026-11-24 21:31:27.724343
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Append-only: the database itself refuses to change or remove audit entries, whatever the
# application does. (The table owner could still drop the trigger; this guards against
# mistakes and application bugs, not against someone with owner access.)
APPEND_ONLY_FUNCTION = """
CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP;
END;
$$
"""

revision: str = "c4e3ce4e1078"
down_revision: str | Sequence[str] | None = "e65756b783e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_username", sa.String(length=40), nullable=True),
        sa.Column("target_type", sa.String(length=20), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_log_actor_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index(
        op.f("ix_audit_log_actor_user_id"), "audit_log", ["actor_user_id"], unique=False
    )
    op.create_index(op.f("ix_audit_log_occurred_at"), "audit_log", ["occurred_at"], unique=False)
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_log_no_update_or_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()"
    )
    op.add_column("shipment_events", sa.Column("actor_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_shipment_events_actor_user_id_users"),
        "shipment_events",
        "users",
        ["actor_user_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_shipment_events_actor_user_id_users"), "shipment_events", type_="foreignkey"
    )
    op.drop_column("shipment_events", "actor_user_id")
    op.drop_index(op.f("ix_audit_log_occurred_at"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_table("audit_log")  # also drops its triggers
    op.execute("DROP FUNCTION audit_log_append_only()")

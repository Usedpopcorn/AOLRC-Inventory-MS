"""add inventory rules and admin events

Revision ID: f7b3c8d9e0a1
Revises: e1a2b3c4d5f6
Create Date: 2026-04-23 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7b3c8d9e0a1"
down_revision = "e1a2b3c4d5f6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    venue_columns = {column["name"] for column in inspector.get_columns("venues")}
    if "stale_threshold_days" not in venue_columns:
        with op.batch_alter_table("venues", schema=None) as batch_op:
            batch_op.add_column(sa.Column("stale_threshold_days", sa.Integer(), nullable=True))

    item_columns = {column["name"] for column in inspector.get_columns("items")}
    if "default_par_level" not in item_columns or "stale_threshold_days" not in item_columns:
        with op.batch_alter_table("items", schema=None) as batch_op:
            if "default_par_level" not in item_columns:
                batch_op.add_column(sa.Column("default_par_level", sa.Integer(), nullable=True))
            if "stale_threshold_days" not in item_columns:
                batch_op.add_column(sa.Column("stale_threshold_days", sa.Integer(), nullable=True))

    existing_tables = set(inspector.get_table_names())
    if "inventory_policies" not in existing_tables:
        op.create_table(
            "inventory_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "default_stale_threshold_days",
                sa.Integer(),
                nullable=False,
                server_default="2",
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "inventory_admin_events" not in existing_tables:
        op.create_table(
            "inventory_admin_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("subject_type", sa.String(length=32), nullable=False),
            sa.Column("subject_id", sa.Integer(), nullable=True),
            sa.Column("subject_label", sa.String(length=255), nullable=True),
            sa.Column("details_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        )
        op.create_index(
            op.f("ix_inventory_admin_events_event_type"),
            "inventory_admin_events",
            ["event_type"],
            unique=False,
        )
        op.create_index(
            op.f("ix_inventory_admin_events_actor_user_id"),
            "inventory_admin_events",
            ["actor_user_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_inventory_admin_events_subject_id"),
            "inventory_admin_events",
            ["subject_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_inventory_admin_events_created_at"),
            "inventory_admin_events",
            ["created_at"],
            unique=False,
        )

    policy_count = bind.execute(sa.text("SELECT COUNT(*) FROM inventory_policies")).scalar()
    if not policy_count:
        bind.execute(
            sa.text(
                """
                INSERT INTO inventory_policies (
                    default_stale_threshold_days,
                    created_at,
                    updated_at
                ) VALUES (
                    2,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            )
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "inventory_admin_events" in existing_tables:
        op.drop_index(op.f("ix_inventory_admin_events_created_at"), table_name="inventory_admin_events")
        op.drop_index(op.f("ix_inventory_admin_events_subject_id"), table_name="inventory_admin_events")
        op.drop_index(op.f("ix_inventory_admin_events_actor_user_id"), table_name="inventory_admin_events")
        op.drop_index(op.f("ix_inventory_admin_events_event_type"), table_name="inventory_admin_events")
        op.drop_table("inventory_admin_events")

    if "inventory_policies" in existing_tables:
        op.drop_table("inventory_policies")

    venue_columns = {column["name"] for column in inspector.get_columns("venues")}
    if "stale_threshold_days" in venue_columns:
        with op.batch_alter_table("venues", schema=None) as batch_op:
            batch_op.drop_column("stale_threshold_days")

    item_columns = {column["name"] for column in inspector.get_columns("items")}
    if "default_par_level" in item_columns or "stale_threshold_days" in item_columns:
        with op.batch_alter_table("items", schema=None) as batch_op:
            if "default_par_level" in item_columns:
                batch_op.drop_column("default_par_level")
            if "stale_threshold_days" in item_columns:
                batch_op.drop_column("stale_threshold_days")

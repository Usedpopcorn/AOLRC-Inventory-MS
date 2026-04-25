"""add order batches and lines

Revision ID: d9a1c3b7e4f2
Revises: c4e7d91a2b6f
Create Date: 2026-04-25 16:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d9a1c3b7e4f2"
down_revision = "c4e7d91a2b6f"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "order_batches" not in tables:
        op.create_table(
            "order_batches",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("batch_type", sa.String(length=32), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_order_batches_batch_type"), "order_batches", ["batch_type"], unique=False)
        op.create_index(
            op.f("ix_order_batches_created_by_user_id"),
            "order_batches",
            ["created_by_user_id"],
            unique=False,
        )
        op.create_index(op.f("ix_order_batches_created_at"), "order_batches", ["created_at"], unique=False)

    if "order_lines" not in tables:
        op.create_table(
            "order_lines",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_batch_id", sa.Integer(), nullable=False),
            sa.Column("item_id", sa.Integer(), nullable=True),
            sa.Column("venue_id", sa.Integer(), nullable=True),
            sa.Column("item_name_snapshot", sa.String(length=120), nullable=False),
            sa.Column("venue_name_snapshot", sa.String(length=120), nullable=False),
            sa.Column("setup_group_code_snapshot", sa.String(length=32), nullable=True),
            sa.Column("setup_group_label_snapshot", sa.String(length=120), nullable=True),
            sa.Column("count_snapshot", sa.Integer(), nullable=True),
            sa.Column("par_snapshot", sa.Integer(), nullable=True),
            sa.Column("suggested_order_qty_snapshot", sa.Integer(), nullable=False),
            sa.Column("over_par_qty_snapshot", sa.Integer(), nullable=False),
            sa.Column("actual_ordered_qty", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["order_batch_id"], ["order_batches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "order_batch_id",
                "venue_id",
                "item_id",
                name="uq_order_lines_batch_venue_item",
            ),
        )
        op.create_index(op.f("ix_order_lines_order_batch_id"), "order_lines", ["order_batch_id"], unique=False)
        op.create_index(op.f("ix_order_lines_item_id"), "order_lines", ["item_id"], unique=False)
        op.create_index(op.f("ix_order_lines_venue_id"), "order_lines", ["venue_id"], unique=False)
        op.create_index(op.f("ix_order_lines_status"), "order_lines", ["status"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "order_lines" in tables:
        op.drop_index(op.f("ix_order_lines_status"), table_name="order_lines")
        op.drop_index(op.f("ix_order_lines_venue_id"), table_name="order_lines")
        op.drop_index(op.f("ix_order_lines_item_id"), table_name="order_lines")
        op.drop_index(op.f("ix_order_lines_order_batch_id"), table_name="order_lines")
        op.drop_table("order_lines")

    if "order_batches" in tables:
        op.drop_index(op.f("ix_order_batches_created_at"), table_name="order_batches")
        op.drop_index(op.f("ix_order_batches_created_by_user_id"), table_name="order_batches")
        op.drop_index(op.f("ix_order_batches_batch_type"), table_name="order_batches")
        op.drop_table("order_batches")

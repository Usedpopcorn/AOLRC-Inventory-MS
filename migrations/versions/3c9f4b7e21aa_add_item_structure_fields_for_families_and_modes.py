"""add item structure fields for families and modes

Revision ID: 3c9f4b7e21aa
Revises: f2a9c31b7d10
Create Date: 2026-04-14 12:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3c9f4b7e21aa"
down_revision = "f2a9c31b7d10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tracking_mode", sa.String(length=40), nullable=False, server_default="quantity"))
        batch_op.add_column(sa.Column("item_category", sa.String(length=40), nullable=False, server_default="consumable"))
        batch_op.add_column(sa.Column("parent_item_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("is_group_parent", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("unit", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_index(batch_op.f("ix_items_parent_item_id"), ["parent_item_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_items_parent_item_id_items"),
            "items",
            ["parent_item_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute(
        """
        UPDATE items
        SET tracking_mode = COALESCE(NULLIF(TRIM(tracking_mode), ''), 'quantity')
        """
    )
    op.execute(
        """
        UPDATE items
        SET item_category = COALESCE(NULLIF(TRIM(item_type), ''), 'consumable')
        """
    )
    op.execute(
        """
        UPDATE items
        SET is_group_parent = COALESCE(is_group_parent, false),
            sort_order = COALESCE(sort_order, 0)
        """
    )

    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.alter_column("tracking_mode", server_default=None)
        batch_op.alter_column("item_category", server_default=None)
        batch_op.alter_column("is_group_parent", server_default=None)
        batch_op.alter_column("sort_order", server_default=None)


def downgrade():
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_items_parent_item_id_items"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_items_parent_item_id"))
        batch_op.drop_column("sort_order")
        batch_op.drop_column("unit")
        batch_op.drop_column("is_group_parent")
        batch_op.drop_column("parent_item_id")
        batch_op.drop_column("item_category")
        batch_op.drop_column("tracking_mode")

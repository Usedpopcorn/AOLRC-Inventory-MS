"""add item setup grouping fields

Revision ID: 8b9d6c4e2f10
Revises: 4f6e2a9c8b11
Create Date: 2026-04-25 11:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8b9d6c4e2f10"
down_revision = "4f6e2a9c8b11"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    item_columns = {col["name"] for col in inspector.get_columns("items")}
    item_indexes = {index["name"] for index in inspector.get_indexes("items")}

    with op.batch_alter_table("items", schema=None) as batch_op:
        if "setup_group_code" not in item_columns:
            batch_op.add_column(sa.Column("setup_group_code", sa.String(length=32), nullable=True))
        if "setup_group_label" not in item_columns:
            batch_op.add_column(sa.Column("setup_group_label", sa.String(length=120), nullable=True))
        if "ix_items_setup_group_code" not in item_indexes:
            batch_op.create_index(batch_op.f("ix_items_setup_group_code"), ["setup_group_code"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    item_columns = {col["name"] for col in inspector.get_columns("items")}
    item_indexes = {index["name"] for index in inspector.get_indexes("items")}

    with op.batch_alter_table("items", schema=None) as batch_op:
        if "ix_items_setup_group_code" in item_indexes:
            batch_op.drop_index(batch_op.f("ix_items_setup_group_code"))
        if "setup_group_label" in item_columns:
            batch_op.drop_column("setup_group_label")
        if "setup_group_code" in item_columns:
            batch_op.drop_column("setup_group_code")

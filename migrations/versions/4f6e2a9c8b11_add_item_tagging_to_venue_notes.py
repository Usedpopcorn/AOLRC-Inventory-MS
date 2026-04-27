"""add item tagging to venue notes

Revision ID: 4f6e2a9c8b11
Revises: f7b3c8d9e0a1
Create Date: 2026-04-24 21:35:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4f6e2a9c8b11"
down_revision = "f7b3c8d9e0a1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    note_columns = {col["name"] for col in inspector.get_columns("venue_notes")}

    if "item_id" not in note_columns:
        with op.batch_alter_table("venue_notes", schema=None) as batch_op:
            batch_op.add_column(sa.Column("item_id", sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f("ix_venue_notes_item_id"), ["item_id"], unique=False)
            batch_op.create_foreign_key(
                batch_op.f("fk_venue_notes_item_id_items"),
                "items",
                ["item_id"],
                ["id"],
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    note_columns = {col["name"] for col in inspector.get_columns("venue_notes")}

    if "item_id" in note_columns:
        with op.batch_alter_table("venue_notes", schema=None) as batch_op:
            batch_op.drop_constraint(batch_op.f("fk_venue_notes_item_id_items"), type_="foreignkey")
            batch_op.drop_index(batch_op.f("ix_venue_notes_item_id"))
            batch_op.drop_column("item_id")

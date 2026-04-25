"""add supply notes

Revision ID: c4e7d91a2b6f
Revises: 8b9d6c4e2f10
Create Date: 2026-04-25 13:15:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4e7d91a2b6f"
down_revision = "8b9d6c4e2f10"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "supply_notes" not in tables:
        op.create_table(
            "supply_notes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("item_id", sa.Integer(), nullable=False),
            sa.Column("author_user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_supply_notes_item_id"), "supply_notes", ["item_id"], unique=False)
        op.create_index(
            op.f("ix_supply_notes_author_user_id"),
            "supply_notes",
            ["author_user_id"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "supply_notes" in tables:
        op.drop_index(op.f("ix_supply_notes_author_user_id"), table_name="supply_notes")
        op.drop_index(op.f("ix_supply_notes_item_id"), table_name="supply_notes")
        op.drop_table("supply_notes")

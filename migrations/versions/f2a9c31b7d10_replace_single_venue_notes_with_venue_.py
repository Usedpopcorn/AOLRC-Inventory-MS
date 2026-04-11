"""replace single venue notes with venue notes table

Revision ID: f2a9c31b7d10
Revises: 7648db90c5d0
Create Date: 2026-04-10 16:42:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2a9c31b7d10"
down_revision = "7648db90c5d0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "venue_notes" not in existing_tables:
        op.create_table(
            "venue_notes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("venue_id", sa.Integer(), nullable=False),
            sa.Column("author_user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_venue_notes_venue_id", "venue_notes", ["venue_id"], unique=False)
        op.create_index("ix_venue_notes_author_user_id", "venue_notes", ["author_user_id"], unique=False)

    venue_columns = {col["name"] for col in inspector.get_columns("venues")}
    if "notes" in venue_columns:
        op.drop_column("venues", "notes")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    venue_columns = {col["name"] for col in inspector.get_columns("venues")}
    if "notes" not in venue_columns:
        op.add_column("venues", sa.Column("notes", sa.Text(), nullable=True))

    if "venue_notes" in existing_tables:
        op.drop_index("ix_venue_notes_author_user_id", table_name="venue_notes")
        op.drop_index("ix_venue_notes_venue_id", table_name="venue_notes")
        op.drop_table("venue_notes")

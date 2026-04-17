"""add count tables

Revision ID: 4c1a0b8a9d3e
Revises: 2d9e571d8543
Create Date: 2026-03-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4c1a0b8a9d3e"
down_revision = "2d9e571d8543"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "count_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "count_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("count_session_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("raw_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["count_session_id"], ["count_sessions.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("count_session_id", "item_id", name="uq_countline_session_item"),
    )

    op.create_table(
        "venue_item_counts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("raw_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venue_id", "item_id", name="uq_venue_item_count"),
    )


def downgrade():
    op.drop_table("venue_item_counts")
    op.drop_table("count_lines")
    op.drop_table("count_sessions")

"""add venue notes

Revision ID: 2d9e571d8543
Revises: 1295ed93f0e3
Create Date: 2026-03-07 18:34:24.977630

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2d9e571d8543'
down_revision = '1295ed93f0e3'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {col["name"] for col in inspector.get_columns("venues")}
    if "notes" not in column_names:
        op.add_column("venues", sa.Column("notes", sa.Text(), nullable=True))

def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {col["name"] for col in inspector.get_columns("venues")}
    if "notes" in column_names:
        op.drop_column("venues", "notes")

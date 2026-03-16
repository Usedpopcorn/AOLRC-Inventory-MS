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
    # Postgres-safe: won't error if the column already exists
    op.execute("ALTER TABLE venues ADD COLUMN IF NOT EXISTS notes TEXT;")

def downgrade():
    # Also safe
    op.execute("ALTER TABLE venues DROP COLUMN IF EXISTS notes;")

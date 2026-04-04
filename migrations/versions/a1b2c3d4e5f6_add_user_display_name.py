"""add user display_name

Revision ID: a1b2c3d4e5f6
Revises: 9f1e2a3b4c5d
Create Date: 2026-04-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "9f1e2a3b4c5d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("display_name", sa.String(length=120), nullable=True))


def downgrade():
    op.drop_column("users", "display_name")

"""add user avatar fields

Revision ID: d3a7b9f5c2e1
Revises: 7648db90c5d0
Create Date: 2026-04-08 11:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3a7b9f5c2e1"
down_revision = "7648db90c5d0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("avatar_filename", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("avatar_updated_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("avatar_updated_at")
        batch_op.drop_column("avatar_filename")

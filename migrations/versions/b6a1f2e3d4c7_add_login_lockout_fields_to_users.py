"""add login lockout fields to users

Revision ID: b6a1f2e3d4c7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b6a1f2e3d4c7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    if "failed_login_attempts" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        )
    if "locked_until" not in existing_columns:
        op.add_column("users", sa.Column("locked_until", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")

"""add user theme preference

Revision ID: f3b2c1d4e5a6
Revises: e6f7a8b9c0d1
Create Date: 2026-04-26 23:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f3b2c1d4e5a6"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "theme_preference" not in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "theme_preference",
                    sa.String(length=20),
                    nullable=False,
                    server_default="purple",
                )
            )

    op.execute(
        sa.text(
            """
            UPDATE users
            SET theme_preference = 'purple'
            WHERE theme_preference IS NULL
               OR lower(trim(theme_preference)) NOT IN ('purple', 'blue')
            """
        )
    )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("theme_preference", server_default=None)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "theme_preference" in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("theme_preference")

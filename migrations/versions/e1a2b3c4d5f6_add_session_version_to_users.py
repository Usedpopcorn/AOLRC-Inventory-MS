"""add session version to users

Revision ID: e1a2b3c4d5f6
Revises: d4f9b6c2a7e1
Create Date: 2026-04-22 01:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1a2b3c4d5f6"
down_revision = "d4f9b6c2a7e1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "session_version" not in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "session_version",
                    sa.Integer(),
                    nullable=False,
                    server_default="1",
                )
            )

        op.execute(
            sa.text(
                """
                UPDATE users
                SET session_version = 1
                WHERE session_version IS NULL OR session_version < 1
                """
            )
        )

        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.alter_column("session_version", server_default=None)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "session_version" in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("session_version")

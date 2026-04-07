"""add user links to activity sessions

Revision ID: c8f4d1a2b3c4
Revises: 4c1a0b8a9d3e
Create Date: 2026-04-07 16:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c8f4d1a2b3c4"
down_revision = "4c1a0b8a9d3e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("checks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_checks_user_id"), ["user_id"], unique=False)
        batch_op.create_foreign_key("fk_checks_user_id_users", "users", ["user_id"], ["id"])

    with op.batch_alter_table("count_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_count_sessions_user_id"), ["user_id"], unique=False)
        batch_op.create_foreign_key("fk_count_sessions_user_id_users", "users", ["user_id"], ["id"])


def downgrade():
    with op.batch_alter_table("count_sessions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_count_sessions_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_count_sessions_user_id"))
        batch_op.drop_column("user_id")

    with op.batch_alter_table("checks", schema=None) as batch_op:
        batch_op.drop_constraint("fk_checks_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_checks_user_id"))
        batch_op.drop_column("user_id")

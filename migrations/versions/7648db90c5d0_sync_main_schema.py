"""sync main schema

Revision ID: 7648db90c5d0
Revises: bf8c239da41d
Create Date: 2026-04-07 22:23:14.637080

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7648db90c5d0'
down_revision = 'bf8c239da41d'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_constraints = {c.get("name") for c in inspector.get_unique_constraints("users")}
    indexes = {i.get("name") for i in inspector.get_indexes("users")}

    with op.batch_alter_table("users", schema=None) as batch_op:
        if "users_email_key" in unique_constraints:
            batch_op.drop_constraint(batch_op.f("users_email_key"), type_="unique")
        if "ix_users_email" in indexes:
            batch_op.drop_index(batch_op.f("ix_users_email"))
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i.get("name") for i in inspector.get_indexes("users")}

    with op.batch_alter_table("users", schema=None) as batch_op:
        if "ix_users_email" in indexes:
            batch_op.drop_index(batch_op.f("ix_users_email"))
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=False)
        batch_op.create_unique_constraint(
            batch_op.f("users_email_key"),
            ["email"],
            postgresql_nulls_not_distinct=False,
        )

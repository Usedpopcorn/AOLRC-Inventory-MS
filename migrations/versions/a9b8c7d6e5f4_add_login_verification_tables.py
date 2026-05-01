"""add login verification tables

Revision ID: a9b8c7d6e5f4
Revises: adf66cac59c1
Create Date: 2026-04-30 20:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a9b8c7d6e5f4"
down_revision = "adf66cac59c1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "last_login_verification_at" not in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("last_login_verification_at", sa.DateTime(), nullable=True))
    if "require_login_verification" not in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "require_login_verification",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.alter_column("require_login_verification", server_default=None)

    existing_tables = set(inspector.get_table_names())
    if "login_verification_challenges" not in existing_tables:
        op.create_table(
            "login_verification_challenges",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
            sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sent_to_email", sa.String(length=255), nullable=True),
            sa.Column("request_ip", sa.String(length=128), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("request_country", sa.String(length=8), nullable=True),
            sa.Column("next_url", sa.String(length=512), nullable=True),
            sa.Column("reason_codes", sa.Text(), nullable=True),
            sa.Column("last_sent_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_login_verification_challenges_user_id",
            "login_verification_challenges",
            ["user_id"],
            unique=False,
        )
        op.create_index(
            "ix_login_verification_challenges_code_hash",
            "login_verification_challenges",
            ["code_hash"],
            unique=False,
        )

    if "trusted_devices" not in existing_tables:
        op.create_table(
            "trusted_devices",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("user_agent_hash", sa.String(length=64), nullable=False),
            sa.Column("user_agent_summary", sa.String(length=255), nullable=True),
            sa.Column("last_ip", sa.String(length=128), nullable=True),
            sa.Column("last_country", sa.String(length=8), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_trusted_devices_user_id", "trusted_devices", ["user_id"], unique=False)
        op.create_index("ix_trusted_devices_token_hash", "trusted_devices", ["token_hash"], unique=True)
        op.create_index(
            "ix_trusted_devices_user_agent_hash",
            "trusted_devices",
            ["user_agent_hash"],
            unique=False,
        )
        op.create_index("ix_trusted_devices_expires_at", "trusted_devices", ["expires_at"], unique=False)
        op.create_index("ix_trusted_devices_revoked_at", "trusted_devices", ["revoked_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "trusted_devices" in existing_tables:
        op.drop_index("ix_trusted_devices_revoked_at", table_name="trusted_devices")
        op.drop_index("ix_trusted_devices_expires_at", table_name="trusted_devices")
        op.drop_index("ix_trusted_devices_user_agent_hash", table_name="trusted_devices")
        op.drop_index("ix_trusted_devices_token_hash", table_name="trusted_devices")
        op.drop_index("ix_trusted_devices_user_id", table_name="trusted_devices")
        op.drop_table("trusted_devices")

    if "login_verification_challenges" in existing_tables:
        op.drop_index(
            "ix_login_verification_challenges_code_hash",
            table_name="login_verification_challenges",
        )
        op.drop_index(
            "ix_login_verification_challenges_user_id",
            table_name="login_verification_challenges",
        )
        op.drop_table("login_verification_challenges")

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "require_login_verification" in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("require_login_verification")
    if "last_login_verification_at" in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("last_login_verification_at")

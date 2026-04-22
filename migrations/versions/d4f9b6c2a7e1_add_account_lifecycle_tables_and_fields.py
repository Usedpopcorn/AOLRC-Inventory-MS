"""add account lifecycle tables and fields

Revision ID: d4f9b6c2a7e1
Revises: 3c9f4b7e21aa
Create Date: 2026-04-21 23:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4f9b6c2a7e1"
down_revision = "3c9f4b7e21aa"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    user_indexes = {index["name"] for index in inspector.get_indexes("users")}
    user_foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("users")}

    with op.batch_alter_table("users", schema=None) as batch_op:
        if "last_login_at" not in user_columns:
            batch_op.add_column(sa.Column("last_login_at", sa.DateTime(), nullable=True))
        if "password_changed_at" not in user_columns:
            batch_op.add_column(sa.Column("password_changed_at", sa.DateTime(), nullable=True))
        if "force_password_change" not in user_columns:
            batch_op.add_column(
                sa.Column(
                    "force_password_change",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "deactivated_at" not in user_columns:
            batch_op.add_column(sa.Column("deactivated_at", sa.DateTime(), nullable=True))
        if "created_by_user_id" not in user_columns:
            batch_op.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        if "deactivated_by_user_id" not in user_columns:
            batch_op.add_column(sa.Column("deactivated_by_user_id", sa.Integer(), nullable=True))
        if "ix_users_created_by_user_id" not in user_indexes:
            batch_op.create_index(batch_op.f("ix_users_created_by_user_id"), ["created_by_user_id"], unique=False)
        if "ix_users_deactivated_by_user_id" not in user_indexes:
            batch_op.create_index(batch_op.f("ix_users_deactivated_by_user_id"), ["deactivated_by_user_id"], unique=False)
        if "fk_users_created_by_user_id_users" not in user_foreign_keys:
            batch_op.create_foreign_key(
                "fk_users_created_by_user_id_users",
                "users",
                ["created_by_user_id"],
                ["id"],
            )
        if "fk_users_deactivated_by_user_id_users" not in user_foreign_keys:
            batch_op.create_foreign_key(
                "fk_users_deactivated_by_user_id_users",
                "users",
                ["deactivated_by_user_id"],
                ["id"],
            )

    op.execute(
        sa.text(
            """
            UPDATE users
            SET password_changed_at = COALESCE(updated_at, created_at)
            WHERE password_changed_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE users
            SET deactivated_at = COALESCE(updated_at, created_at)
            WHERE active = 0 AND deactivated_at IS NULL
            """
        )
    )

    if not inspector.has_table("password_action_tokens"):
        op.create_table(
            "password_action_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index(op.f("ix_password_action_tokens_user_id"), "password_action_tokens", ["user_id"], unique=False)
        op.create_index(op.f("ix_password_action_tokens_created_by_user_id"), "password_action_tokens", ["created_by_user_id"], unique=False)
        op.create_index(op.f("ix_password_action_tokens_token_hash"), "password_action_tokens", ["token_hash"], unique=True)

    if not inspector.has_table("account_audit_events"):
        op.create_table(
            "account_audit_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("target_user_id", sa.Integer(), nullable=True),
            sa.Column("target_email", sa.String(length=255), nullable=True),
            sa.Column("details_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_account_audit_events_event_type"), "account_audit_events", ["event_type"], unique=False)
        op.create_index(op.f("ix_account_audit_events_actor_user_id"), "account_audit_events", ["actor_user_id"], unique=False)
        op.create_index(op.f("ix_account_audit_events_target_user_id"), "account_audit_events", ["target_user_id"], unique=False)
        op.create_index(op.f("ix_account_audit_events_created_at"), "account_audit_events", ["created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("account_audit_events"):
        account_indexes = {index["name"] for index in inspector.get_indexes("account_audit_events")}
        for index_name in (
            "ix_account_audit_events_created_at",
            "ix_account_audit_events_target_user_id",
            "ix_account_audit_events_actor_user_id",
            "ix_account_audit_events_event_type",
        ):
            if index_name in account_indexes:
                op.drop_index(index_name, table_name="account_audit_events")
        op.drop_table("account_audit_events")

    if inspector.has_table("password_action_tokens"):
        token_indexes = {index["name"] for index in inspector.get_indexes("password_action_tokens")}
        for index_name in (
            "ix_password_action_tokens_token_hash",
            "ix_password_action_tokens_created_by_user_id",
            "ix_password_action_tokens_user_id",
        ):
            if index_name in token_indexes:
                op.drop_index(index_name, table_name="password_action_tokens")
        op.drop_table("password_action_tokens")

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    user_indexes = {index["name"] for index in inspector.get_indexes("users")}
    user_foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("users")}

    with op.batch_alter_table("users", schema=None) as batch_op:
        if "fk_users_deactivated_by_user_id_users" in user_foreign_keys:
            batch_op.drop_constraint("fk_users_deactivated_by_user_id_users", type_="foreignkey")
        if "fk_users_created_by_user_id_users" in user_foreign_keys:
            batch_op.drop_constraint("fk_users_created_by_user_id_users", type_="foreignkey")
        if "ix_users_deactivated_by_user_id" in user_indexes:
            batch_op.drop_index(batch_op.f("ix_users_deactivated_by_user_id"))
        if "ix_users_created_by_user_id" in user_indexes:
            batch_op.drop_index(batch_op.f("ix_users_created_by_user_id"))
        if "deactivated_by_user_id" in user_columns:
            batch_op.drop_column("deactivated_by_user_id")
        if "created_by_user_id" in user_columns:
            batch_op.drop_column("created_by_user_id")
        if "deactivated_at" in user_columns:
            batch_op.drop_column("deactivated_at")
        if "force_password_change" in user_columns:
            batch_op.drop_column("force_password_change")
        if "password_changed_at" in user_columns:
            batch_op.drop_column("password_changed_at")
        if "last_login_at" in user_columns:
            batch_op.drop_column("last_login_at")

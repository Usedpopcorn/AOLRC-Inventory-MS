"""add feedback submissions

Revision ID: e6f7a8b9c0d1
Revises: d9a1c3b7e4f2
Create Date: 2026-04-25 20:15:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "d9a1c3b7e4f2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "feedback_submissions" not in tables:
        op.create_table(
            "feedback_submissions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("submission_type", sa.String(length=32), nullable=False),
            sa.Column("summary", sa.String(length=160), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source_path", sa.String(length=255), nullable=False),
            sa.Column("source_query", sa.Text(), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("submitter_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["submitter_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_feedback_submissions_submission_type"),
            "feedback_submissions",
            ["submission_type"],
            unique=False,
        )
        op.create_index(
            op.f("ix_feedback_submissions_submitter_user_id"),
            "feedback_submissions",
            ["submitter_user_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_feedback_submissions_created_at"),
            "feedback_submissions",
            ["created_at"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "feedback_submissions" in tables:
        op.drop_index(op.f("ix_feedback_submissions_created_at"), table_name="feedback_submissions")
        op.drop_index(
            op.f("ix_feedback_submissions_submitter_user_id"),
            table_name="feedback_submissions",
        )
        op.drop_index(
            op.f("ix_feedback_submissions_submission_type"),
            table_name="feedback_submissions",
        )
        op.drop_table("feedback_submissions")

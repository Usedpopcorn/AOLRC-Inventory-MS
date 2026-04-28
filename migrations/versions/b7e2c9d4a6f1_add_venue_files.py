"""add venue files

Revision ID: b7e2c9d4a6f1
Revises: adf66cac59c1
Create Date: 2026-04-28 02:45:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b7e2c9d4a6f1"
down_revision = "adf66cac59c1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "venue_files" not in tables:
        op.create_table(
            "venue_files",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("venue_id", sa.Integer(), nullable=False),
            sa.Column("uploaded_by_user_id", sa.Integer(), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("stored_filename", sa.String(length=255), nullable=False),
            sa.Column("mime_type", sa.String(length=120), nullable=False),
            sa.Column("extension", sa.String(length=24), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=40), nullable=False),
            sa.Column("preview_type", sa.String(length=40), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("stored_filename"),
        )
        op.create_index(op.f("ix_venue_files_venue_id"), "venue_files", ["venue_id"], unique=False)
        op.create_index(
            op.f("ix_venue_files_uploaded_by_user_id"),
            "venue_files",
            ["uploaded_by_user_id"],
            unique=False,
        )
        op.create_index(op.f("ix_venue_files_extension"), "venue_files", ["extension"], unique=False)
        op.create_index(op.f("ix_venue_files_category"), "venue_files", ["category"], unique=False)
        op.create_index(op.f("ix_venue_files_created_at"), "venue_files", ["created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "venue_files" in tables:
        op.drop_index(op.f("ix_venue_files_created_at"), table_name="venue_files")
        op.drop_index(op.f("ix_venue_files_category"), table_name="venue_files")
        op.drop_index(op.f("ix_venue_files_extension"), table_name="venue_files")
        op.drop_index(op.f("ix_venue_files_uploaded_by_user_id"), table_name="venue_files")
        op.drop_index(op.f("ix_venue_files_venue_id"), table_name="venue_files")
        op.drop_table("venue_files")

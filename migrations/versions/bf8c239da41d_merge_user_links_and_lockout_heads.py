"""merge user-links and lockout heads

Revision ID: bf8c239da41d
Revises: b6a1f2e3d4c7, c8f4d1a2b3c4
Create Date: 2026-04-07 20:14:43.942122

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bf8c239da41d'
down_revision = ('b6a1f2e3d4c7', 'c8f4d1a2b3c4')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

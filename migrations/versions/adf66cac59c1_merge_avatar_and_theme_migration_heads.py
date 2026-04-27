"""Merge avatar and theme migration heads

Revision ID: adf66cac59c1
Revises: d3a7b9f5c2e1, f3b2c1d4e5a6
Create Date: 2026-04-27 13:20:15.454792

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'adf66cac59c1'
down_revision = ('d3a7b9f5c2e1', 'f3b2c1d4e5a6')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

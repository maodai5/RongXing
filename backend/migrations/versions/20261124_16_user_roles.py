"""user roles (T19)

Revision ID: 20261124_16
Revises: 20261119_15
"""
from alembic import op
import sqlalchemy as sa

revision = '20261124_16'
down_revision = '20261119_15'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('role', sa.String(16), server_default='user', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'role')

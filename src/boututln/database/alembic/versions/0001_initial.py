"""Initial schema (v1).

Revision ID: 0001
Revises:
"""

from alembic import op

from boututln.database import schema_v1

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema_v1.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    schema_v1.metadata.drop_all(bind=op.get_bind())

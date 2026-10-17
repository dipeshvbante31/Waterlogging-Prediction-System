"""add predictions.location_name (optional human-readable place for a prediction)

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("predictions") as b:
        b.add_column(sa.Column("location_name", sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table("predictions") as b:
        b.drop_column("location_name")

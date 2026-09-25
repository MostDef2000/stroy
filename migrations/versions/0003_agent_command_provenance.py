"""agent command provenance

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("design_commands") as batch:
        batch.add_column(sa.Column("model_profile", sa.String(160)))
        batch.add_column(sa.Column("correlation_id", sa.String(128)))


def downgrade() -> None:
    with op.batch_alter_table("design_commands") as batch:
        batch.drop_column("correlation_id")
        batch.drop_column("model_profile")

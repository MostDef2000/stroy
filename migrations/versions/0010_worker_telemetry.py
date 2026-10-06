"""worker telemetry samples

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workers") as batch:
        batch.add_column(sa.Column("telemetry", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("telemetry_updated_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("workers") as batch:
        batch.drop_column("telemetry_updated_at")
        batch.drop_column("telemetry")

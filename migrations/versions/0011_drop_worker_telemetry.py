"""drop worker telemetry columns

The telemetry feature never went live: no worker sends telemetry samples and
the only candidate worker machine cannot collect them (Windows, no /proc).
This removes the columns added by 0010.

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workers") as batch:
        batch.drop_column("telemetry_updated_at")
        batch.drop_column("telemetry")


def downgrade() -> None:
    # Re-add with the exact types 0010 used so rolling back to pre-0011 code
    # keeps working.
    with op.batch_alter_table("workers") as batch:
        batch.add_column(sa.Column("telemetry", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("telemetry_updated_at", sa.DateTime(timezone=True), nullable=True)
        )

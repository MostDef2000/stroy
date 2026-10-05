"""auth session idle tracking

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch:
        batch.add_column(
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
        )
    # Backfill so sessions that predate idle tracking start their idle window
    # from creation instead of being treated as never-seen and killed.
    op.execute("UPDATE auth_sessions SET last_seen_at = created_at WHERE last_seen_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_column("last_seen_at")

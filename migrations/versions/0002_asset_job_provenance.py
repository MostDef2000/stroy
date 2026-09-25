"""asset lineage and durable job provenance

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.add_column(
            sa.Column("role", sa.String(40), nullable=False, server_default="apartment")
        )
        batch.add_column(
            sa.Column(
                "metadata_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "source_asset_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(sa.Column("duplicate_of_asset_id", sa.String(36)))

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(160)))
        batch.add_column(
            sa.Column(
                "progress",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(sa.Column("correlation_id", sa.String(128)))
        batch.add_column(
            sa.Column(
                "runtime_provenance",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.create_unique_constraint(
            "uq_jobs_idempotency",
            ["project_id", "job_type", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("uq_jobs_idempotency", type_="unique")
        batch.drop_column("runtime_provenance")
        batch.drop_column("correlation_id")
        batch.drop_column("progress")
        batch.drop_column("idempotency_key")

    with op.batch_alter_table("assets") as batch:
        batch.drop_column("duplicate_of_asset_id")
        batch.drop_column("source_asset_ids")
        batch.drop_column("metadata_json")
        batch.drop_column("role")

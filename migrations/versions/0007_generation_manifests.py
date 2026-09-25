"""generation manifest persistence

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_manifests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("job_id", sa.String(36), nullable=False, unique=True),
        sa.Column("scene_revision_id", sa.String(36), nullable=False),
        sa.Column("design_revision_id", sa.String(36), nullable=False),
        sa.Column("camera_id", sa.String(255), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_generation_manifests_project_id",
        "generation_manifests",
        ["project_id"],
    )
    op.create_index(
        "ix_generation_manifests_job_id",
        "generation_manifests",
        ["job_id"],
        unique=True,
    )
    op.create_index(
        "ix_generation_manifests_scene_revision_id",
        "generation_manifests",
        ["scene_revision_id"],
    )
    op.create_index(
        "ix_generation_manifests_design_revision_id",
        "generation_manifests",
        ["design_revision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_manifests_design_revision_id",
        table_name="generation_manifests",
    )
    op.drop_index(
        "ix_generation_manifests_scene_revision_id",
        table_name="generation_manifests",
    )
    op.drop_index(
        "ix_generation_manifests_job_id",
        table_name="generation_manifests",
    )
    op.drop_index(
        "ix_generation_manifests_project_id",
        table_name="generation_manifests",
    )
    op.drop_table("generation_manifests")

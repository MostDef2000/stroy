"""generation manifests and geometry diagnostics

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_manifests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False, unique=True),
        sa.Column("scene_revision_id", sa.String(36), nullable=False),
        sa.Column("design_revision_id", sa.String(36), nullable=False),
        sa.Column("camera_id", sa.String(255), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_generation_manifests_project_id", "generation_manifests", ["project_id"])
    op.create_index("ix_generation_manifests_job_id", "generation_manifests", ["job_id"], unique=True)
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

    op.create_table(
        "geometry_diagnostics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("generation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("scene_revision_id", sa.String(36), nullable=False),
        sa.Column("camera_id", sa.String(255), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("input_asset_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_geometry_diagnostics_project_id", "geometry_diagnostics", ["project_id"])
    op.create_index("ix_geometry_diagnostics_generation_id", "geometry_diagnostics", ["generation_id"], unique=True)
    op.create_index("ix_geometry_diagnostics_scene_revision_id", "geometry_diagnostics", ["scene_revision_id"])


def downgrade() -> None:
    op.drop_index("ix_geometry_diagnostics_scene_revision_id", table_name="geometry_diagnostics")
    op.drop_index("ix_geometry_diagnostics_generation_id", table_name="geometry_diagnostics")
    op.drop_index("ix_geometry_diagnostics_project_id", table_name="geometry_diagnostics")
    op.drop_table("geometry_diagnostics")

    op.drop_index("ix_generation_manifests_design_revision_id", table_name="generation_manifests")
    op.drop_index("ix_generation_manifests_scene_revision_id", table_name="generation_manifests")
    op.drop_index("ix_generation_manifests_job_id", table_name="generation_manifests")
    op.drop_index("ix_generation_manifests_project_id", table_name="generation_manifests")
    op.drop_table("generation_manifests")

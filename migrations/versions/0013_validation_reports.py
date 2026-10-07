"""validation reports persistence

Spatial validation reports (R2): one row per computed run, keyed by scene
revision + config hash. Additive only: one new table and its lookup indexes;
no existing table or column changes.

Revision ID: 0013
Revises: 0012
"""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("scene_revision_id", sa.String(64), nullable=False),
        sa.Column("scene_content_hash", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_validation_reports_project_id",
        "validation_reports",
        ["project_id"],
    )
    op.create_index(
        "ix_validation_reports_scene_revision_id",
        "validation_reports",
        ["scene_revision_id"],
    )
    op.create_index(
        "ix_validation_reports_scene_content_hash",
        "validation_reports",
        ["scene_content_hash"],
    )
    op.create_index(
        "ix_validation_reports_config_hash",
        "validation_reports",
        ["config_hash"],
    )
    op.create_index(
        "ix_validation_reports_report_hash",
        "validation_reports",
        ["report_hash"],
    )
    op.create_index(
        "ix_validation_reports_project_revision_created",
        "validation_reports",
        ["project_id", "scene_revision_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_validation_reports_project_revision_created",
        table_name="validation_reports",
    )
    op.drop_index("ix_validation_reports_report_hash", table_name="validation_reports")
    op.drop_index("ix_validation_reports_config_hash", table_name="validation_reports")
    op.drop_index(
        "ix_validation_reports_scene_content_hash", table_name="validation_reports"
    )
    op.drop_index(
        "ix_validation_reports_scene_revision_id", table_name="validation_reports"
    )
    op.drop_index("ix_validation_reports_project_id", table_name="validation_reports")
    op.drop_table("validation_reports")

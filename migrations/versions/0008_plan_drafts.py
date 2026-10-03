"""plan draft persistence

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("draft_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_plan_drafts_project_id",
        "plan_drafts",
        ["project_id"],
    )
    op.create_index(
        "ix_plan_drafts_project_version",
        "plan_drafts",
        ["project_id", "version"],
    )


def downgrade() -> None:
    op.drop_index("ix_plan_drafts_project_version", table_name="plan_drafts")
    op.drop_index("ix_plan_drafts_project_id", table_name="plan_drafts")
    op.drop_table("plan_drafts")

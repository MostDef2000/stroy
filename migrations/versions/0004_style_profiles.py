"""style profile persistence

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "style_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("source_asset_ids", sa.JSON(), nullable=False),
        sa.Column("source_text", sa.Text()),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("model_profile", sa.String(160)),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_style_profiles_project_id",
        "style_profiles",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_style_profiles_project_id", table_name="style_profiles")
    op.drop_table("style_profiles")

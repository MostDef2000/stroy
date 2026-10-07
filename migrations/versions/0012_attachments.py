"""attachment persistence

Project-scoped attachments (photo/note/file/task) that pin to the project
itself, a room or a scene entity. Additive only: one new table and its
lookup indexes; no existing table or column changes.

Revision ID: 0012
Revises: 0011
"""

from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_attachments_project_id",
        "attachments",
        ["project_id"],
    )
    op.create_index(
        "ix_attachments_project_target",
        "attachments",
        ["project_id", "target_type", "target_id"],
    )
    op.create_index(
        "ix_attachments_asset_id",
        "attachments",
        ["asset_id"],
    )
    op.create_index(
        "ix_attachments_project_kind",
        "attachments",
        ["project_id", "kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_attachments_project_kind", table_name="attachments")
    op.drop_index("ix_attachments_asset_id", table_name="attachments")
    op.drop_index("ix_attachments_project_target", table_name="attachments")
    op.drop_index("ix_attachments_project_id", table_name="attachments")
    op.drop_table("attachments")

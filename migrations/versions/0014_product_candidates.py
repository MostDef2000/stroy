"""product candidates persistence

Product candidates (R3): furniture items imported from a product URL or
entered manually, on their way to being placed into the scene. Additive only:
one new table and its lookup indexes; no existing table or column changes.

Revision ID: 0014
Revises: 0013
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        # Real FKs to assets: the project cascade must delete candidates
        # before assets (services.projects._CHILD_TABLES ordering).
        sa.Column(
            "source_asset_id",
            sa.String(36),
            sa.ForeignKey("assets.id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("brand", sa.String(160), nullable=True),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(12), nullable=True),
        sa.Column("width_mm", sa.Float(), nullable=True),
        sa.Column("depth_mm", sa.Float(), nullable=True),
        sa.Column("height_mm", sa.Float(), nullable=True),
        sa.Column("material_descriptors", sa.Text(), nullable=True),
        sa.Column("color_descriptors", sa.Text(), nullable=True),
        sa.Column("provenance", sa.String(20), nullable=False),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column(
            "preview_asset_id",
            sa.String(36),
            sa.ForeignKey("assets.id"),
            nullable=True,
        ),
        sa.Column("three_d_ref", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_product_candidates_project_id",
        "product_candidates",
        ["project_id"],
    )
    op.create_index(
        "ix_product_candidates_project_created",
        "product_candidates",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_product_candidates_source_asset_id",
        "product_candidates",
        ["source_asset_id"],
    )
    op.create_index(
        "ix_product_candidates_preview_asset_id",
        "product_candidates",
        ["preview_asset_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_candidates_preview_asset_id", table_name="product_candidates"
    )
    op.drop_index(
        "ix_product_candidates_source_asset_id", table_name="product_candidates"
    )
    op.drop_index(
        "ix_product_candidates_project_created", table_name="product_candidates"
    )
    op.drop_index(
        "ix_product_candidates_project_id", table_name="product_candidates"
    )
    op.drop_table("product_candidates")

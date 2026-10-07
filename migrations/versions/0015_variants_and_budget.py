"""scene variants + budget items

R4: scene variants (isolated design branches over immutable revisions) and
variant-scoped budget items.  Also removes the R1-era UNIQUE lock on
``scene_revisions.parent_revision_id`` so a revision can have multiple
children (variant branching); linear-history protection moves to the
expected-head optimistic guard in ``services.variants``.

The parent UNIQUE constraint is UNNAMED (backend-generated): the constraint
name is resolved through the SQLAlchemy inspector at runtime, never
hardcoded.  Downgrade is conditional and fails loudly once variant rows or a
branching revision graph exist — linear history cannot be re-locked under
them.

Revision ID: 0015
Revises: 0014
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _find_parent_unique_constraint(inspector: sa.Inspector) -> dict | None:
    """Locate the (unnamed) UNIQUE constraint on parent_revision_id."""
    for uc in inspector.get_unique_constraints("scene_revisions"):
        if list(uc.get("column_names") or []) == ["parent_revision_id"]:
            return uc
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    found = _find_parent_unique_constraint(inspector)
    if found is None:
        raise RuntimeError(
            "migration 0015: expected a UNIQUE constraint on "
            "scene_revisions.parent_revision_id but none was found; refusing "
            "to upgrade an unexpected schema (drift?)"
        )
    name = found.get("name")
    if name is None:
        # Backend-generated unnamed constraint (e.g. SQLite autoindex):
        # batch-recreate the table from its reflected definition minus the
        # constraint (named drops are impossible without a name). The change
        # lives entirely in the copy_from definition, so the batch must be
        # forced to recreate (zero emitted operations would skip it under
        # recreate="auto").
        meta = sa.MetaData()
        table = sa.Table("scene_revisions", meta, autoload_with=bind)
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.UniqueConstraint) and list(
                constraint.columns
            ) == [table.c.parent_revision_id]:
                table.constraints.remove(constraint)
        with op.batch_alter_table(
            "scene_revisions", copy_from=table, recreate="always"
        ):
            pass  # recreation itself applies the definition change
    else:
        with op.batch_alter_table("scene_revisions") as batch:
            batch.drop_constraint(name, type_="unique")
    # The dropped UNIQUE constraint was the only parent lookup structure;
    # replace it with a plain index for parent-chain walks.
    op.create_index(
        "ix_scene_revisions_parent_revision_id",
        "scene_revisions",
        ["parent_revision_id"],
    )

    op.create_table(
        "scene_variants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("base_scene_revision_id", sa.String(36), nullable=False),
        sa.Column("head_scene_revision_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_scene_variants_project_id", "scene_variants", ["project_id"]
    )
    op.create_index(
        "ix_scene_variants_project_status",
        "scene_variants",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_scene_variants_project_head",
        "scene_variants",
        ["project_id", "head_scene_revision_id"],
    )
    op.create_index(
        "ix_scene_variants_project_base",
        "scene_variants",
        ["project_id", "base_scene_revision_id"],
    )

    # Variant linkage on manifests; legacy rows stay NULL.
    op.add_column(
        "render_manifests", sa.Column("variant_id", sa.String(36), nullable=True)
    )
    op.create_index(
        "ix_render_manifests_variant_id", "render_manifests", ["variant_id"]
    )
    op.add_column(
        "generation_manifests", sa.Column("variant_id", sa.String(36), nullable=True)
    )
    op.create_index(
        "ix_generation_manifests_variant_id", "generation_manifests", ["variant_id"]
    )

    op.create_table(
        "budget_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        # Plain string reference (no FK): variant deletion stays row-only.
        sa.Column("variant_id", sa.String(36), nullable=False),
        # Explicit binding: the variant-head revision at creation time.
        sa.Column("scene_revision_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "product_candidate_id",
            sa.String(36),
            sa.ForeignKey("product_candidates.id"),
            nullable=True,
        ),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(12), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_budget_items_project_id", "budget_items", ["project_id"])
    op.create_index("ix_budget_items_variant_id", "budget_items", ["variant_id"])
    op.create_index(
        "ix_budget_items_project_variant",
        "budget_items",
        ["project_id", "variant_id"],
    )
    op.create_index(
        "ix_budget_items_variant_revision",
        "budget_items",
        ["variant_id", "scene_revision_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    variant_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM scene_variants")
    ).scalar_one()
    if variant_count:
        raise RuntimeError(
            f"downgrade 0015 refused: {variant_count} scene variant row(s) "
            "exist; variant data would be lost. Archive/delete variants first."
        )
    forked = bind.execute(
        sa.text(
            "SELECT parent_revision_id FROM scene_revisions "
            "WHERE parent_revision_id IS NOT NULL "
            "GROUP BY parent_revision_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).scalar_one_or_none()
    if forked is not None:
        raise RuntimeError(
            "downgrade 0015 refused: scene revision "
            f"{forked} has more than one child (branching history); the "
            "pre-0015 UNIQUE parent lock cannot be restored over a branched "
            "graph."
        )

    op.drop_table("budget_items")
    op.drop_index("ix_render_manifests_variant_id", table_name="render_manifests")
    op.drop_column("render_manifests", "variant_id")
    op.drop_index(
        "ix_generation_manifests_variant_id", table_name="generation_manifests"
    )
    op.drop_column("generation_manifests", "variant_id")
    op.drop_table("scene_variants")
    op.drop_index(
        "ix_scene_revisions_parent_revision_id", table_name="scene_revisions"
    )
    # Re-create the linear-history lock (unnamed, backend-generated — the
    # pre-0015 constraint had no explicit name).
    if bind.dialect.name == "sqlite":
        # SQLite has no ALTER for constraints: recreate the table with the
        # unnamed UNIQUE restored.
        meta = sa.MetaData()
        table = sa.Table("scene_revisions", meta, autoload_with=bind)
        table.append_constraint(sa.UniqueConstraint("parent_revision_id"))
        with op.batch_alter_table(
            "scene_revisions", copy_from=table, recreate="always"
        ):
            pass
    else:
        op.create_unique_constraint(None, "scene_revisions", ["parent_revision_id"])

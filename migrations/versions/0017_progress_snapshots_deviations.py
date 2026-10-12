"""progress snapshots + deviations

R12 (#162): renovation progress snapshots (named site observations pinned to
ONE immutable scene revision — canonical latest or an approved variant head,
resolved at creation and never re-resolved) and planned-vs-actual deviations
against them. Additive only: two new tables plus their lookup indexes; no
existing table is touched. Deviations are owner-recorded (no photo inference)
and progress data lives outside ``scene_json`` (canonical_hash untouched).

``deviations.snapshot_id`` and the planned id columns are plain string
references (no FK): snapshot deletion is soft (``deleted_at``) and dangling
planned entity/room ids are allowed and surfaced by compare.

Revision ID: 0017
Revises: 0016
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "progress_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("stage_label", sa.String(120), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("attachment_ids", sa.JSON(), nullable=False, server_default="[]"),
        # canonical | variant.
        sa.Column("compare_target_kind", sa.String(20), nullable=False),
        # Plain string reference to scene_variants.id (NULL for canonical).
        sa.Column("compare_target_variant_id", sa.String(36), nullable=True),
        # Pinned immutable revision, resolved once at creation.
        sa.Column("compare_target_revision_id", sa.String(36), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        # Soft delete marker; attachments/deviations stay untouched.
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_progress_snapshots_project_id", "progress_snapshots", ["project_id"]
    )
    op.create_index(
        "ix_progress_snapshots_project_created",
        "progress_snapshots",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_progress_snapshots_project_stage",
        "progress_snapshots",
        ["project_id", "stage"],
    )
    op.create_index(
        "ix_progress_snapshots_project_target",
        "progress_snapshots",
        ["project_id", "compare_target_kind", "compare_target_revision_id"],
    )

    op.create_table(
        "deviations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        # Plain string reference (no FK): snapshot soft delete stays
        # row-only and never cascades into deviations.
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        # open | confirmed | accepted | discarded.
        sa.Column("status", sa.String(20), nullable=False),
        # Copied from the snapshot's pinned target at creation.
        sa.Column("planned_revision_id", sa.String(36), nullable=False),
        # scene | room | entity.
        sa.Column("planned_target_type", sa.String(20), nullable=False),
        # Plain string references (no FK): dangling planned ids are allowed
        # and surfaced as "dangling" by compare.
        sa.Column("planned_entity_id", sa.String(255), nullable=True),
        sa.Column("planned_room_id", sa.String(255), nullable=True),
        sa.Column("planned_property_path", sa.String(255), nullable=True),
        sa.Column("planned_value_json", sa.JSON(), nullable=True),
        # missing | unexpected | property_diff | state_diff | note.
        sa.Column("observed_kind", sa.String(30), nullable=False),
        sa.Column("observed_value_json", sa.JSON(), nullable=False),
        # observed | manually_measured | model_inferred | confirmed.
        sa.Column("provenance", sa.String(30), nullable=False),
        # low | medium | high.
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column(
            "evidence_attachment_ids", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column("note", sa.Text(), nullable=True),
        # Filled by the accept endpoint only; NULL otherwise.
        sa.Column("accepted_target_kind", sa.String(20), nullable=True),
        sa.Column("accepted_variant_id", sa.String(36), nullable=True),
        sa.Column("accepted_command_id", sa.String(36), nullable=True),
        sa.Column("accepted_revision_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deviations_project_id", "deviations", ["project_id"])
    op.create_index("ix_deviations_snapshot_id", "deviations", ["snapshot_id"])
    op.create_index(
        "ix_deviations_project_snapshot_status",
        "deviations",
        ["project_id", "snapshot_id", "status"],
    )
    op.create_index(
        "ix_deviations_project_planned_entity",
        "deviations",
        ["project_id", "planned_entity_id"],
    )
    op.create_index(
        "ix_deviations_project_accepted_revision",
        "deviations",
        ["project_id", "accepted_revision_id"],
    )


def downgrade() -> None:
    op.drop_table("deviations")
    op.drop_table("progress_snapshots")

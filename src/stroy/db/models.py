from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from stroy.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # R8 designer brief (#198): owner-curated free-text notes stored as
    # {needs_wishes, questions_to_discuss, updated_at}. NULL until the first
    # PATCH; back to NULL when both fields are cleared (never {}).
    brief_notes_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SceneRevisionRow(Base):
    __tablename__ = "scene_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    # R4: plain index only. The R1-era UNIQUE lock (one child per parent) was
    # dropped by migration 0015 so scene variants can branch a revision into
    # siblings; linear-history protection now lives in the service layer
    # (expected-head checks), not in the schema.
    parent_revision_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    command_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="0.1.0")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    scene_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DesignCommandRow(Base):
    __tablename__ = "design_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    base_revision_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(255))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    reference_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(20))
    request_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_profile: Mapped[str | None] = mapped_column(String(160), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssetRow(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    provenance: Mapped[str] = mapped_column(String(40), default="user")
    role: Mapped[str] = mapped_column(String(40), default="apartment")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    duplicate_of_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProductCandidateRow(Base):
    """R3 product candidate: a furniture item being imported into the project.

    Provenance tracks the edit history of the facts:
    ``extracted`` (URL import, unedited), ``manual`` (hand-entered), ``mixed``
    (extracted facts later edited by the owner).
    """

    __tablename__ = "product_candidates"
    __table_args__ = (
        # Composite lookup: newest-first candidate listing per project.
        Index("ix_product_candidates_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    # Origin URL of an extraction (NULL for fully manual candidates).
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Manual candidates may be seeded from an uploaded asset (e.g. a photo of
    # the product); real FK so project cascade must remove candidates first.
    source_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id"), nullable=True, index=True
    )
    # Extracted/user-entered facts; all nullable (partial imports persist).
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    width_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    depth_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    material_descriptors: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_descriptors: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[str] = mapped_column(String(20), default="manual")
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Preview image downloaded by the import pipeline into the asset store.
    preview_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id"), nullable=True, index=True
    )
    # Opaque vendor/product reference for a future 3D model lookup (v1: passthrough).
    three_d_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AttachmentRow(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        # Composite lookup: list attachments of one target inside a project.
        Index("ix_attachments_project_target", "project_id", "target_type", "target_id"),
        # Composite lookup: filter by kind within a project (checklists etc.).
        Index("ix_attachments_project_kind", "project_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    # Polymorphic target: "project" (target_id NULL), "room" or "entity".
    target_type: Mapped[str] = mapped_column(String(20))
    # Nullable by design: project-level attachments have no target row.
    # String reference (no FK) so a plan recommit that rewrites the scene
    # never orphans the row; dangling targets are surfaced by the API.
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    # String reference to assets.id (kept as plain column so an asset can be
    # deleted without cascading the note attached to it).
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StyleProfileRow(Base):
    __tablename__ = "style_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="0.1.0")
    source_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_json: Mapped[dict] = mapped_column(JSON)
    model_profile: Mapped[str | None] = mapped_column(String(160), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanDraftRow(Base):
    __tablename__ = "plan_drafts"
    __table_args__ = (
        Index("ix_plan_drafts_project_version", "project_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    draft_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationManifestRow(Base):
    __tablename__ = "generation_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    scene_revision_id: Mapped[str] = mapped_column(String(36), index=True)
    design_revision_id: Mapped[str] = mapped_column(String(36), index=True)
    camera_id: Mapped[str] = mapped_column(String(255))
    # R4: variant linkage (NULL for legacy/canonical-scoped generations).
    variant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    manifest_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RenderManifestRow(Base):
    __tablename__ = "render_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    scene_revision_id: Mapped[str] = mapped_column(String(36), index=True)
    design_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    camera_id: Mapped[str] = mapped_column(String(255))
    # R4: variant linkage (NULL for legacy/canonical-scoped renders).
    variant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    manifest_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GeometryDiagnosticRow(Base):
    __tablename__ = "geometry_diagnostics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    scene_revision_id: Mapped[str] = mapped_column(String(36), index=True)
    camera_id: Mapped[str] = mapped_column(String(255))
    reference_asset_id: Mapped[str] = mapped_column(String(36))
    generated_asset_id: Mapped[str] = mapped_column(String(36))
    diagnostic_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "created_at"),
        UniqueConstraint(
            "project_id",
            "job_type",
            "idempotency_key",
            name="uq_jobs_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    required_capabilities: Mapped[list] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runtime_provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    leased_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkerRow(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    models: Mapped[list] = mapped_column(JSON, default=list)
    runtimes: Mapped[dict] = mapped_column(JSON, default=dict)
    hardware: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="online")
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ValidationReportRow(Base):
    __tablename__ = "validation_reports"
    __table_args__ = (
        # Composite lookup: latest report for one scene revision in a project.
        Index(
            "ix_validation_reports_project_revision_created",
            "project_id",
            "scene_revision_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    scene_revision_id: Mapped[str] = mapped_column(String(64), index=True)
    scene_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    report_hash: Mapped[str] = mapped_column(String(64), index=True)
    report_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SceneVariantRow(Base):
    """R4 scene variant: an isolated design branch over immutable revisions.

    Lineage is the variant head plus the parent-chain walk over
    ``scene_revisions`` (revisions stay immutable and shared with the
    canonical scene). ``base_scene_revision_id`` is the fork point;
    ``head_scene_revision_id`` is the variant's current tip. Variant-scoped
    appends create sibling revisions under the old unique-lock schema's
    successor: the expected-head optimistic guard lives in
    ``services.variants`` (migration 0015 dropped the DB-level UNIQUE).
    """

    __tablename__ = "scene_variants"
    __table_args__ = (
        Index("ix_scene_variants_project_status", "project_id", "status"),
        Index("ix_scene_variants_project_head", "project_id", "head_scene_revision_id"),
        Index("ix_scene_variants_project_base", "project_id", "base_scene_revision_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    base_scene_revision_id: Mapped[str] = mapped_column(String(36))
    head_scene_revision_id: Mapped[str] = mapped_column(String(36))
    # draft | shortlisted | approved | archived (state machine in services.variants).
    status: Mapped[str] = mapped_column(String(20), default="draft")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BudgetItemRow(Base):
    """R4 budget line bound to one variant at creation time.

    ``scene_revision_id`` records the variant-head revision the item was
    created against (explicit binding, never re-resolved to "latest").
    ``variant_id`` is a plain string reference (no FK) so variant deletion
    stays a row-only operation; ``product_candidate_id`` is a real FK, so the
    project cascade must remove budget items BEFORE product candidates.
    """

    __tablename__ = "budget_items"
    __table_args__ = (
        Index("ix_budget_items_project_variant", "project_id", "variant_id"),
        Index("ix_budget_items_variant_revision", "variant_id", "scene_revision_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    variant_id: Mapped[str] = mapped_column(String(36), index=True)
    scene_revision_id: Mapped[str] = mapped_column(String(36))
    # candidate | lighting | manual | material (validated in services.budget).
    kind: Mapped[str] = mapped_column(String(20))
    product_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_candidates.id"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Required when amount is present (service-validated; default RUB).
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Takeoff parameters for material items: coverage_unit (m2|linear_m|each),
    # waste_factor, package_size, unit_price, target_id.
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

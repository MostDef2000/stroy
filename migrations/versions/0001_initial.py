"""initial STROY control-plane tables"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "scene_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("parent_revision_id", sa.String(36), unique=True),
        sa.Column("command_id", sa.String(36)),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("scene_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scene_revisions_project_id", "scene_revisions", ["project_id"])
    op.create_index("ix_scene_revisions_content_hash", "scene_revisions", ["content_hash"])
    op.create_table(
        "design_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("base_revision_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("reference_asset_ids", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("request_text", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_design_commands_project_id", "design_commands", ["project_id"])
    op.create_table(
        "assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False, unique=True),
        sa.Column("original_name", sa.String(255)),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("provenance", sa.String(40), nullable=False),
        sa.Column("source_asset_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assets_project_id", "assets", ["project_id"])
    op.create_index("ix_assets_sha256", "assets", ["sha256"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36)),
        sa.Column("job_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("leased_to", sa.String(100)),
        sa.Column("lease_id", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "created_at"])
    op.create_table(
        "workers",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("display_name", sa.String(200)),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column("runtimes", sa.JSON(), nullable=False),
        sa.Column("hardware", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("auth_sessions")
    op.drop_table("workers")
    op.drop_table("jobs")
    op.drop_table("assets")
    op.drop_table("design_commands")
    op.drop_table("scene_revisions")
    op.drop_table("projects")

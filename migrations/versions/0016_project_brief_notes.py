"""project brief notes

R8 designer brief (#198): a single nullable JSON column on ``projects``
holding the owner-curated designer-brief notes
(``{needs_wishes, questions_to_discuss, updated_at}``).  No default: rows
created before (and after) the migration carry NULL until the first
brief-notes PATCH; a PATCH that clears both fields stores NULL again
(never ``{}``), so "no notes" has exactly one representation.

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("brief_notes_json", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "brief_notes_json")

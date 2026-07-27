"""documents unique constraint (user_id, doc_version_hash) -- FR-6 dedup race fix

Revision ID: 007_documents_unique_constraint
Revises: 006_decisions_table
Create Date: 2026-07-27

Closes the FR-6 upload-dedup race documented as a known, accepted gap in
DECISION_LOG.md (2026-07-20): the app-level SELECT-then-INSERT dedup check
in POST /documents/upload has a narrow window where two concurrent uploads
of the identical file by the same user could both pass the SELECT and both
INSERT. This constraint makes the DB the final arbiter -- the second INSERT
in any such race now fails with IntegrityError instead of creating a
duplicate row.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "007_documents_unique_constraint"
down_revision = "006_decisions_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_documents_user_version", "documents", ["user_id", "doc_version_hash"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_documents_user_version", "documents", type_="unique")

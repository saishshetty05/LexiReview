"""is_synthetic column on documents + document_summaries table

Revision ID: 002_is_synthetic_and_summaries
Revises: 001_schema_v1
Create Date: 2026-07-18

Adopts CONTRACTS.md v1.3: §1a (`documents.is_synthetic`, fail-closed default
FALSE — untagged documents are treated as real, so the free-tier
SYNTHETIC_ONLY gate refuses them by default) and §2b (`document_summaries`,
one row per (doc_version_hash, model_version), written once by the worker
after analysis).

`document_summaries` follows the same immutability design as
`analysis_results`/`documents` from migration 001: ENABLE+FORCE RLS with the
standard user_isolation policy, app_user granted SELECT+INSERT only (no
UPDATE/DELETE) — CONTRACTS.md §2b rejected a nullable column on `documents`
for exactly this reason (app_user has no UPDATE grant on documents, so it
would be unwritable after the initial insert).
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "002_is_synthetic_and_summaries"
down_revision = "001_schema_v1"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"

# Same predicate as every other user-scoped table (migration 001).
_USER_SCOPED_POLICY = "user_id = current_setting('app.user_id', true)::uuid"


def upgrade() -> None:
    # ── documents.is_synthetic — CONTRACTS.md §1a, fail-closed default ───
    op.add_column(
        "documents",
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ── document_summaries — CONTRACTS.md §2b ─────────────────────────
    op.create_table(
        "document_summaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("doc_version_hash", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "doc_version_hash", "model_version", name="uq_document_summaries_hash_model"
        ),
    )

    op.execute("ALTER TABLE document_summaries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_summaries FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY user_isolation ON document_summaries USING ({_USER_SCOPED_POLICY})")

    op.execute(f"GRANT SELECT, INSERT ON document_summaries TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation ON document_summaries")
    op.drop_table("document_summaries")
    op.drop_column("documents", "is_synthetic")

"""analysis_costs table — CONTRACTS.md §11 (v1.17), PRD FR-16

Revision ID: 014_analysis_costs
Revises: 013_admin_security_definer
Create Date: 2026-09-04

One row per real LLM provider call (analyze/summarize/entailment), written
by the worker. Backs both halves of FR-16: the monthly quota check in
upload_document derives its count from `COUNT(DISTINCT job_id)` over this
table (§11: "you're charged for what actually called the LLM, nothing
else"), and the table itself is the "per-analysis token and cost logging
for operational visibility" the PRD text asks for.

Immutable-artifact pattern (SELECT+INSERT only, no UPDATE/DELETE), same as
analysis_results/document_summaries — a cost row is a fact about a call
that already happened, never edited afterward.

Metadata only, per CLAUDE.md rule 2 and §11's explicit DECIDED note: tokens,
model string, job_id, timestamps. No prompt or completion text is ever
written here, even transiently.

`(user_id, created_at)` index is load-bearing, not speculative: the quota
count in upload_document runs on every upload, filtered on exactly these
two columns.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "014_analysis_costs"
down_revision = "013_admin_security_definer"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"

# Post-migration-005 fail-closed predicate (unguarded current_setting).
_USER_SCOPED_POLICY = "user_id = current_setting('app.user_id')::uuid"


def upgrade() -> None:
    op.create_table(
        "analysis_costs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("analysis_jobs.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("call_type", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_analysis_costs_user_id_created_at",
        "analysis_costs",
        ["user_id", "created_at"],
    )

    op.execute("ALTER TABLE analysis_costs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analysis_costs FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY user_isolation ON analysis_costs USING ({_USER_SCOPED_POLICY})")

    op.execute(f"GRANT SELECT, INSERT ON analysis_costs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation ON analysis_costs")
    op.drop_index("ix_analysis_costs_user_id_created_at", table_name="analysis_costs")
    op.drop_table("analysis_costs")

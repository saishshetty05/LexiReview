"""decisions table — CONTRACTS.md §7 (v1.7), PRD FR-13

Revision ID: 006_decisions_table
Revises: 005_rls_fail_closed
Create Date: 2026-07-26

Real backend persistence for reviewer accept/dismiss decisions, replacing
the client-side-only localStorage stopgap #47 shipped (PROJECT_STATUS.md
§5). One row per (user_id, finding_id); `finding_id` FKs into
`analysis_results.id`.

Unlike `analysis_results`/`document_summaries` (immutable, INSERT-only),
`decisions` is a MUTABLE table — a reviewer can change their mind — so it
follows the `analysis_jobs`/`users` grant pattern (SELECT, INSERT, UPDATE,
no DELETE) rather than the immutable-artifact pattern.

RLS predicate uses the post-migration-005 fail-closed form (unguarded
current_setting, no `true`/missing_ok arg) — NOT migration 002's
now-superseded predicate.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "006_decisions_table"
down_revision = "005_rls_fail_closed"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"

# Post-005 fail-closed predicate (see 005_rls_fail_closed_on_never_set.py).
_USER_SCOPED_POLICY = "user_id = current_setting('app.user_id')::uuid"


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "finding_id", sa.Uuid(), sa.ForeignKey("analysis_results.id"), nullable=False
        ),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "finding_id", name="uq_decisions_user_finding"),
    )

    op.execute("ALTER TABLE decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE decisions FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY user_isolation ON decisions USING ({_USER_SCOPED_POLICY})")

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON decisions TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation ON decisions")
    op.drop_table("decisions")

"""analysis_jobs.summary_error — pre-created ahead of the worker-summary-trigger wiring

Revision ID: 004_summary_error
Revises: 003_users_email_lookup
Create Date: 2026-07-20

CONTRACTS.md §2c (v1.4) already locks this column's shape and semantics:
"Worker-trigger failure recorded via a new nullable analysis_jobs.summary_error
column (same category:message format as error_reason column)". This migration
implements exactly that -- no new decision, just landing the schema ahead of
the worker logic (Person B's PR) so that PR doesn't have to bundle a
migration with worker code, per the lane-separation convention already
established for A's schema lane / B's analysis lane.

NULL means "no summary attempt failure" -- covers both "summary generation
succeeded" and "summary generation was never attempted" (e.g. a job that
failed before reaching the post-_execute_analysis summary step). Both are
valid, unremarkable states per the v1.4 worker-trigger design: summary
generation is a single best-effort attempt, not a first-class tracked
entity (the v1.4 decision also rejected a separate document_summary_attempts
table for the same reason).

No new grant: migration 001 already grants
`GRANT SELECT, INSERT, UPDATE ON analysis_jobs TO app_user` at the table
level -- verified in migration 001 before assuming it -- and Postgres
table-level grants cover columns added later via ALTER TABLE ADD COLUMN
with no re-grant needed.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "004_summary_error"
down_revision = "003_users_email_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column("summary_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_jobs", "summary_error")

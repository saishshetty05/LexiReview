"""RLS defense-in-depth: fail closed on app.user_id NEVER set, not just set-invalid

Revision ID: 005_rls_fail_closed
Revises: 004_summary_error
Create Date: 2026-07-20

Follow-up to PR #38 (docs/DECISION_LOG.md 2026-07-20, "CRITICAL FIX"). #38's
root cause: code that bypassed app_user_session and used a plain
SessionLocal() directly, so app.user_id was never SET LOCAL at all for that
session. Every user_isolation/self_only RLS policy predicate was
`user_id = current_setting('app.user_id', true)::uuid` -- the `true`
("missing_ok") argument makes current_setting return NULL, not error, when
the GUC was never touched this session. `NULL::uuid` is NULL, and
`user_id = NULL` is NULL (never TRUE), so the query silently returned ZERO
rows instead of raising -- #38 shipped unnoticed because "no rows" and
"correctly scoped, no rows for this user" look identical from the caller's
side, and nothing failed loudly.

The 2026-07-16 decision (docs/DECISION_LOG.md) already established fail-
closed behavior for a NARROWER case: app.user_id explicitly SET LOCAL to an
empty/invalid string, which raises "invalid input syntax for type uuid"
today, unchanged by this migration. It did not cover "never SET at all,"
which turned out to be the more likely real mistake (#38) and is what this
migration closes.

DECIDED -- fix: drop the `true` (missing_ok) argument, so
`current_setting('app.user_id')` (unguarded) raises Postgres's own
"unrecognized configuration parameter" error the moment ANY query hits an
RLS-scoped table in a session that never called SET LOCAL app.user_id --
regardless of whether that session went through app_user_session or bypassed
it entirely (exactly what #38 did). This is a DB-layer backstop, the same
philosophy as the users.self_only policy itself ("Backstops constitution
rule 1 at the DB layer even if app code ever omits a user_id filter",
2026-07-15) -- it protects every current and future code path, not just the
ones that remember to use app_user_session correctly.

VERIFIED, not assumed (same empirical standard as #38's own fix): manually
tested against the running dev Postgres before writing this migration.
users has two PERMISSIVE policies (self_only + migration 003's email_lookup,
FOR SELECT USING (true), OR-combined for SELECT). Confirmed directly:
- SELECT with app.user_id completely unset still succeeds (both a
  positive match and a correct zero-row miss) -- self_only's now-erroring
  predicate does NOT break the OR, because email_lookup's unconditional
  `true` alone satisfies it. Login (which relies on exactly this path,
  app_user_session(None)) is unaffected.
- INSERT with app.user_id completely unset (governed by self_only ALONE --
  email_lookup is FOR SELECT only) now raises
  "unrecognized configuration parameter" as intended.

Applies to every user_isolation/self_only policy: documents, analysis_jobs,
analysis_results, audit_log, document_summaries (migration 002), and
users.self_only. users.email_lookup (migration 003) is untouched -- it
never referenced current_setting to begin with.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "005_rls_fail_closed"
down_revision = "004_summary_error"
branch_labels = None
depends_on = None

_USER_ISOLATION_TABLES = (
    "documents",
    "analysis_jobs",
    "analysis_results",
    "audit_log",
    "document_summaries",
)

_OLD_PREDICATE = "user_id = current_setting('app.user_id', true)::uuid"
_NEW_PREDICATE = "user_id = current_setting('app.user_id')::uuid"

_OLD_SELF_ONLY = "id = current_setting('app.user_id', true)::uuid"
_NEW_SELF_ONLY = "id = current_setting('app.user_id')::uuid"


def upgrade() -> None:
    for table in _USER_ISOLATION_TABLES:
        op.execute(f"ALTER POLICY user_isolation ON {table} USING ({_NEW_PREDICATE})")
    op.execute(f"ALTER POLICY self_only ON users USING ({_NEW_SELF_ONLY})")


def downgrade() -> None:
    for table in _USER_ISOLATION_TABLES:
        op.execute(f"ALTER POLICY user_isolation ON {table} USING ({_OLD_PREDICATE})")
    op.execute(f"ALTER POLICY self_only ON users USING ({_OLD_SELF_ONLY})")

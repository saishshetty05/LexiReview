"""schema v1: users, documents, analysis_jobs, analysis_results, audit_log
+ FORCE RLS + non-superuser app_user role

Revision ID: 001_schema_v1
Revises:
Create Date: 2026-07-15

Adopts CONTRACTS.md §1 (analysis_jobs, unchanged from Person B's model) and
§2 (analysis_results Storage). RLS design follows the 2026-07-15 RLS spike
finding in docs/DECISION_LOG.md: `lexireview` (the owner/migration role) is
superuser + BYPASSRLS, which bypasses RLS unconditionally even with FORCE set
— so a dedicated non-superuser, non-BYPASSRLS role (`app_user`) is created
here and is the ONLY role the running application connects as.

Grant design enforces immutability at the database layer, not just in app
code (CLAUDE.md rule 9; CONTRACTS.md §2 "findings are immutable once
written"): app_user gets SELECT+INSERT only (no UPDATE/DELETE) on documents,
analysis_results, and audit_log. analysis_jobs gets SELECT+INSERT+UPDATE
because the worker transitions job state in place (CONTRACTS.md §1
writer rules) — the "API writes queued-only, worker owns transitions" split
is enforced in application code, not by separate DB roles, since both API
and worker connect as the same app_user.

Account-deletion cascade (FR-15 / DPDP 24h SLA) is DELIBERATELY not
grantable to app_user: hard-deleting a user's rows requires bypassing the
INSERT-only immutability grants on documents/analysis_results/audit_log.
That cascade must run via a separate privileged connection (the owner role,
or a future dedicated `deletion_user` role) — never via app_user. This is a
decision, not an omission; see docs/DECISION_LOG.md 2026-07-15.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "001_schema_v1"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "app_user"

# RLS policy predicate shared by every user-scoped table.
_USER_SCOPED_POLICY = "user_id = current_setting('app.user_id', true)::uuid"


def _app_role_password() -> str:
    # Read at migration time only — never hardcoded, never logged (CLAUDE.md rule 5/10).
    password = os.environ.get("APP_USER_PASSWORD")
    if not password:
        raise RuntimeError(
            "APP_USER_PASSWORD is not set. Migration 001 creates the app_user "
            "role and must be given a password via this env var — see .env.example."
        )
    return password


def upgrade() -> None:
    bind = op.get_bind()

    # ── app_user role: non-superuser, non-BYPASSRLS, LOGIN only ──────────
    # Idempotent-ish guard: CREATE ROLE has no IF NOT EXISTS, so check first.
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
    ).scalar()
    if not role_exists:
        bind.execute(
            sa.text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD :pw"),
            {"pw": _app_role_password()},
        )

    # ── users ──────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── documents (immutable versions) ────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("doc_version_hash", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("file_type", sa.String(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("doc_id", "version", name="uq_documents_doc_id_version"),
    )

    # ── analysis_jobs — CONTRACTS.md §1, columns match app.models.AnalysisJob exactly ──
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("doc_version_hash", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── analysis_results — CONTRACTS.md §2 Storage ────────────────────
    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("analysis_jobs.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("doc_version_hash", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("verification", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── audit_log — content-free metadata only (CLAUDE.md rule 2) ────
    # ip is purged after 90 days by a future scheduled job (not built here).
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("doc_id", sa.Uuid(), nullable=True),
        sa.Column("ip", INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── RLS: ENABLE + FORCE on every user-data table, plus users itself ──
    # FORCE is required even for the table owner (ENABLE alone lets the
    # owner see everything) — see the 2026-07-15 spike finding.
    for table in ("documents", "analysis_jobs", "analysis_results", "audit_log"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY user_isolation ON {table} USING ({_USER_SCOPED_POLICY})")

    # users: self-row-only visibility. A session can only see the user row
    # matching its own current_setting('app.user_id') — this holds even if
    # application code ever forgets a WHERE filter on a users query.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY self_only ON users "
        "USING (id = current_setting('app.user_id', true)::uuid)"
    )

    # ── Grants: app_user is INSERT+SELECT only on immutable tables (no
    # UPDATE/DELETE at the DB level enforces CLAUDE.md rule 9 and the
    # CONTRACTS.md §2 "findings are immutable" rule). analysis_jobs gets
    # UPDATE because the worker transitions state in place; users gets
    # UPDATE for profile changes. No table grants DELETE to app_user —
    # account-deletion cascade runs via a separate privileged connection,
    # never via app_user (see module docstring and DECISION_LOG.md).
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON users TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON documents TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON analysis_jobs TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON analysis_results TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {APP_ROLE}")


def downgrade() -> None:
    for table in ("audit_log", "analysis_results", "analysis_jobs", "documents", "users"):
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS self_only ON {table}")

    op.drop_table("audit_log")
    op.drop_table("analysis_results")
    op.drop_table("analysis_jobs")
    op.drop_table("documents")
    op.drop_table("users")

    op.execute(f"DROP ROLE IF EXISTS {APP_ROLE}")

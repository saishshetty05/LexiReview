"""outbox table + dedicated relay role — CONTRACTS.md §1/§5 (v1.13)

Revision ID: 011_outbox_relay
Revises: 010_refresh_tokens
Create Date: 2026-09-01

The schema half of the broker-enqueue reliability fix (the last OPEN item in
PROJECT_STATUS.md §5, closed in contract by CONTRACTS.md v1.13): the API will
write the `analysis_jobs` row and a matching `outbox` row atomically, and a
relay (not the API) owns broker delivery. This migration only creates the
table, the relay role, and the grants — the `upload_document`/relay behavior
lands in a follow-up implementation PR. Purely additive; nothing here touches
existing tables.

## No RLS — a deliberate exception to the FORCE-RLS-everywhere convention

The relay sweeps undelivered rows across ALL users (`delivered_at IS NULL`),
and per locked CONTRACTS.md §1 the outbox has no `user_id` column. Every other
table is scoped by `current_setting('app.user_id')` RLS, which is per-user by
construction and cannot express a cross-user scan. So the outbox is access-
controlled by GRANTS alone, not RLS:

  - `app_user` gets INSERT only — the upload writes the row; the API never
    reads or mutates the outbox.
  - `relay` (new role) gets SELECT + UPDATE only — it discovers undelivered
    rows and stamps attempts/`last_attempt_at`/`delivered_at`.

`relay` is deliberately non-superuser, non-BYPASSRLS, and granted NOTHING
except the outbox table — it cannot read documents/findings/users (unlike the
owner role). A compromised relay reads delivery metadata only.

## ON DELETE CASCADE on outbox.job_id

`delete_account_cascade` (app/auth.py) deletes a user's `analysis_jobs` rows
by `user_id`, but outbox has no `user_id` column to key a by-user delete on.
CASCADE resolves that without touching auth.py: deleting a user's jobs removes
their outbox rows automatically, and the relay therefore never attempts
delivery for a deleted user's job. This is safe despite the repo's general
no-cascade stance (migration 001 / DECISION_LOG 2026-07-15), which exists to
protect immutable user data and the audit trail — the outbox is ephemeral
delivery-queue infrastructure, not user data.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "011_outbox_relay"
down_revision = "010_refresh_tokens"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"
RELAY_ROLE = "relay"


def _relay_role_password() -> str:
    # Read at migration time only — never hardcoded, never logged (CLAUDE.md rule 5/10).
    password = os.environ.get("RELAY_USER_PASSWORD")
    if not password:
        raise RuntimeError(
            "RELAY_USER_PASSWORD is not set. Migration 011 creates the relay "
            "role and must be given a password via this env var — see .env.example."
        )
    return password


def upgrade() -> None:
    bind = op.get_bind()

    # ── relay role: non-superuser, non-BYPASSRLS, LOGIN only ──────────────
    # Idempotent-ish guard: CREATE ROLE has no IF NOT EXISTS, so check first
    # (same pattern as migration 001's app_user).
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": RELAY_ROLE}
    ).scalar()
    if not role_exists:
        # CREATE ROLE ... PASSWORD is DDL — Postgres does not accept bind
        # parameters there. Safe here because the password is operator-
        # controlled (env var), not user input; quotes are still doubled
        # defensively (same as migration 001).
        escaped_password = _relay_role_password().replace("'", "''")
        bind.execute(sa.text(f"CREATE ROLE {RELAY_ROLE} LOGIN PASSWORD '{escaped_password}'"))

    # ── outbox — columns exactly per CONTRACTS.md §1 (v1.13) ─────────────
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "payload_json",
            JSONB(),
            nullable=False,
            comment="the send_task payload (analyze_document, args=[doc_id, user_id]); opaque to the API — CONTRACTS.md §1",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Note: no ENABLE/FORCE ROW LEVEL SECURITY and no user_isolation policy —
    # see the module docstring. Access is governed by grants below.

    op.execute(f"GRANT INSERT ON outbox TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE ON outbox TO {RELAY_ROLE}")


def downgrade() -> None:
    op.drop_table("outbox")
    op.execute(f"DROP ROLE IF EXISTS {RELAY_ROLE}")

"""outbox table + dedicated relay role — transactional outbox (CONTRACTS.md §1/§5, v1.13)

Revision ID: 011_outbox_relay
Revises: 010_refresh_tokens
Create Date: 2026-09-01

Implements the schema half of the outbox design locked in CONTRACTS.md §1/§5
(v1.13, PR #85) that closes the broker-unreachable queued-forever gap: the API
writes `analysis_jobs` + `outbox` rows atomically, and a relay (not the API)
owns broker delivery. This migration is purely additive — no
`upload_document`/`_enqueue_analysis` behavior change and no relay/sweeper
code (those land in a follow-up implementation PR now that the schema exists).

## RLS / access model — the deliberate exception to FORCE-RLS-everywhere

Every other table uses FORCE ROW LEVEL SECURITY scoped by
`current_setting('app.user_id')`. The outbox has **no `user_id` column** by
locked contract (§1), and the relay sweeps undelivered rows across **all
users** (`delivered_at IS NULL`) — so user-scoped RLS is structurally
inexpressible for a cross-user sweeper. The only workable access control is
**grants-only**: no ENABLE/FORCE RLS on `outbox`, access fully governed by
role grants.

Therefore a dedicated least-privilege **`relay`** role is created here
(non-superuser, non-BYPASSRLS, LOGIN), granted `SELECT, UPDATE` on `outbox`
ONLY — a compromised relay reads delivery metadata (job_id, attempts,
delivered_at), never documents/findings/emails/user PII. `app_user` gets
`INSERT` on `outbox` only (the upload writes it; the API never reads or
mutates the outbox). See docs/MIGRATION_011_RLS_DECISION.md (signed 2026-09-01)
and docs/DECISION_LOG.md for the full evaluation of the three alternatives.

Do NOT "fix" this into FORCE RLS — it would break the cross-user relay.

## ON DELETE CASCADE

`delete_account_cascade` (backend/app/auth.py) deletes analysis_jobs by
user_id, but outbox has no user_id column so the cascade function can't key
outbox rows by user. `ON DELETE CASCADE` on outbox.job_id resolves this:
deleting a user's jobs auto-removes their outbox rows, so **no change to
auth.py is needed** and the relay never attempts delivery for a deleted
user's job. This mirrors the repo's "cascade is in the owner's delete path"
intent — 001's no-cascade rule is about immutable user data/audit, not
ephemeral delivery-queue rows.
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


def _relay_password() -> str:
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

    # ── relay role: non-superuser, non-BYPASSRLS, LOGIN only ─────────────
    # Idempotent-ish guard: CREATE ROLE has no IF NOT EXISTS, so check first
    # (mirrors migration 001's app_user pattern).
    relay_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": RELAY_ROLE}
    ).scalar()
    if not relay_exists:
        # CREATE ROLE ... PASSWORD is DDL — Postgres does not accept bind
        # parameters there, so the literal must be escaped and inlined.
        # Safe because the password is operator-controlled (env var), not
        # user input; quotes are still doubled defensively.
        escaped_password = _relay_password().replace("'", "''")
        bind.execute(
            sa.text(f"CREATE ROLE {RELAY_ROLE} LOGIN PASSWORD '{escaped_password}'")
        )

    # ── outbox — columns exactly per CONTRACTS.md §1 ────────────────────
    # One outbox row per analysis job (job_id UNIQUE). Deliberately NO
    # ENABLE/FORCE RLS — see module docstring and the signed decision memo.
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
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── Grants (grants-only access control — no RLS here) ───────────────
    # app_user: INSERT only — the upload writes the row; the API never reads
    # or mutates the outbox.
    op.execute(f"GRANT INSERT ON outbox TO {APP_ROLE}")
    # relay: SELECT (cross-user sweep) + UPDATE (attempts/delivered_at) only.
    op.execute(f"GRANT SELECT, UPDATE ON outbox TO {RELAY_ROLE}")


def downgrade() -> None:
    op.drop_table("outbox")
    op.execute(f"DROP ROLE IF EXISTS {RELAY_ROLE}")

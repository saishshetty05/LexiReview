"""refresh_tokens table — CONTRACTS.md §9 (v1.11)

Revision ID: 010_refresh_tokens
Revises: 009_users_mfa_columns
Create Date: 2026-08-27

Rotating refresh tokens, closing the app/auth.py TODO open since #25
(15-minute access token, no renewal path). Only a SHA-256 hash of the raw
token is ever stored (app/auth.py's create_refresh_token) -- unlike
mfa_secret (migration 009), a refresh token is only ever compared, never
read back, so there is no reason to keep it reversible.

`refresh_token_lookup` mirrors migration 003's `email_lookup` pre-auth
exception: SELECT-only, USING (true), no app.user_id dependency -- needed
because POST /auth/refresh must find the row before it knows which user it
belongs to (same bootstrapping problem login's email lookup solves).
Same caveat as 003: any future SELECT against this table must filter
explicitly (WHERE token_hash = ...) rather than relying on RLS to scope the
result. Policy combination is command-scoped (003's docstring) -- adding
this SELECT-only policy does not affect INSERT/UPDATE, which stay governed
by user_isolation alone, so rotation's revoke-then-insert still requires
app.user_id to be set.

Mutable via revoked_at (rotation/logout/reuse-detection), same
SELECT+INSERT+UPDATE-no-DELETE grant pattern as `decisions` (migration 006)
rather than the immutable-artifact tables.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "010_refresh_tokens"
down_revision = "009_users_mfa_columns"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"

# Post-migration-005 fail-closed predicate (unguarded current_setting).
_USER_SCOPED_POLICY = "user_id = current_setting('app.user_id')::uuid"


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute("ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY user_isolation ON refresh_tokens USING ({_USER_SCOPED_POLICY})")
    op.execute("CREATE POLICY refresh_token_lookup ON refresh_tokens FOR SELECT USING (true)")

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON refresh_tokens TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS refresh_token_lookup ON refresh_tokens")
    op.execute("DROP POLICY IF EXISTS user_isolation ON refresh_tokens")
    op.drop_table("refresh_tokens")

"""users: add mfa_secret and mfa_enabled for TOTP-based MFA

Revision ID: 008_users_mfa_columns
Revises: 007_documents_unique_constraint
Create Date: 2026-08-22

MFA is opt-in (never mandatory). mfa_secret stores the TOTP secret
(base32-encoded) when a user sets up MFA; nullable because MFA may not
be configured. mfa_enabled is the gate — only when TRUE does login
require a TOTP challenge. Both columns are user-scoped via existing RLS.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "009_users_mfa_columns"
down_revision = "008_decisions_severity_override"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"

# Post-migration-005 fail-closed predicate (unguarded current_setting).
_USER_SCOPED_POLICY = "user_id = current_setting('app.user_id')::uuid"


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret", sa.String(), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))

    # RLS is already enabled on users from migration 001; the existing
    # user_isolation policy (user_id = current_setting(...)::uuid) covers
    # new columns automatically since it's a row-level predicate. No new
    # policy needed.

    # Grants: users table already has SELECT, INSERT, UPDATE for app_user
    # (migration 001). No additional grants needed.


def downgrade() -> None:
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_secret")

"""Admin role schema — is_admin, active, audit_log.target_user_id

Revision ID: 012_admin_role
Revises: 011_outbox_relay
Create Date: 2026-09-02

First of three PRs resuming the paused admin-role work (paused 2026-08-12).
This migration is purely additive schema — no behavior changes, no new
grants, no SECURITY DEFINER functions (those land in PR #2).

## Columns added

- `users.is_admin` BOOLEAN NOT NULL DEFAULT FALSE: flags a user as admin.
  Existing rows get FALSE (no one is admin by default; the first admin is
  promoted via a manual DB bootstrap — one-off SQL UPDATE, documented in a
  runbook, no self-serve promotion).

- `users.active` BOOLEAN NOT NULL DEFAULT TRUE: flags whether a user account
  is active. Existing rows get TRUE (everyone is active by default). A
  suspended user has active=FALSE; `get_current_user` (PR #2) checks this
  column and rejects the request. No second RLS policy on users — the
  SECURITY DEFINER functions (PR #2) handle cross-user admin reads/writes.

- `audit_log.target_user_id` UUID NULL, no FK: the user an admin action
  targets. Matches the `doc_id` nullable-no-FK precedent (migration 001).
  No FK because the target user may be deleted later; the audit row should
  survive that deletion.

## Why no new grants

`app_user` already has `SELECT, INSERT, UPDATE` on `users` (migration 001)
and `SELECT, INSERT` on `audit_log` (migration 001). The new columns are
accessible through existing grants — no `GRANT` statement needed.

## Why no RLS changes

`users` already has FORCE RLS with `self_only` policy
(`id = current_setting('app.user_id', true)::uuid`). The new columns are
just data on existing rows. The SECURITY DEFINER functions (PR #2) will
bypass RLS for admin cross-user reads — no new policy is needed here.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "012_admin_role"
down_revision = "011_outbox_relay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users: two new boolean columns for admin role + account suspension.
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default="true"))

    # audit_log: target_user_id for admin actions (nullable, no FK — matches
    # doc_id precedent; the target user may be deleted later).
    op.add_column("audit_log", sa.Column("target_user_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "target_user_id")
    op.drop_column("users", "active")
    op.drop_column("users", "is_admin")

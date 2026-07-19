"""users.email_lookup RLS policy — pre-auth exception #1 of 2

Revision ID: 003_users_email_lookup
Revises: 002_is_synthetic_and_summaries
Create Date: 2026-07-19

DELIBERATE, NARROW CRACK IN "every query is user_id-scoped" (CLAUDE.md rule 1).
See docs/DECISION_LOG.md 2026-07-19 for the twin of this exception: the
account-deletion cascade's use of the owner connection (app/auth.py,
delete_account_cascade). Both exist because two auth operations happen
BEFORE app.user_id can be known: login (has only an email, not yet a user_id
to SET LOCAL) and this policy's counterpart use in register's would-be
duplicate check (not actually used there — see app/auth.py, register()
relies on the users.email UNIQUE constraint instead, so no SELECT is needed
pre-insert).

`self_only` (migration 001) is USING-only with no FOR clause, so it governs
ALL commands (SELECT/INSERT/UPDATE/DELETE) by default. Adding a second
PERMISSIVE policy restricted to `FOR SELECT` combines with `self_only` via
OR *for SELECT only* — Postgres policy combination is command-scoped, so
INSERT/UPDATE/DELETE on `users` remain governed by `self_only` alone,
unaffected by this policy. The practical effect: any SELECT against `users`
as app_user now returns ALL rows regardless of `app.user_id` — this is the
ONLY table/command where that is true. Any future code reading `users` must
filter explicitly (e.g. `WHERE email = :email`) instead of relying on RLS to
scope the result, unlike every other table in the schema.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "003_users_email_lookup"
down_revision = "002_is_synthetic_and_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE POLICY email_lookup ON users FOR SELECT USING (true)")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS email_lookup ON users")

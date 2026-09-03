"""SECURITY DEFINER functions for admin cross-user access

Revision ID: 013_admin_security_definer
Revises: 012_admin_role
Create Date: 2026-09-03

PR #2 of 3: adds three SECURITY DEFINER functions that bypass RLS for admin
operations. SECURITY DEFINER means the function executes with the privileges of
its owner (the migration/owner role, which has BYPASSRLS) — so it can read/write
across user rows without needing a second RLS policy on users.

Each function internally re-checks is_admin on the calling user to prevent
privilege escalation: a non-admin who somehow gets EXECUTE on the function gets
a clear error, not silent cross-user access.

## Functions created

1. `current_user_is_admin()` → BOOLEAN
   Reads is_admin for the calling user (via current_setting('app.user_id')).
   Currently unused by the app layer (require_admin in main.py reads the ORM
   User object directly). Exists for future direct-SQL / admin tooling use.

2. `admin_list_users()` → SETOF RECORD
   Returns (id, email, is_admin, active, created_at) for ALL users.
   SECURITY DEFINER bypasses self_only RLS so the admin sees every row.
   Called by the /admin/users list endpoint (PR #3).

3. `admin_set_user_active(target_user_id UUID, active BOOLEAN) → VOID
   Sets the active column for a target user. Validates:
   - Caller is admin (re-checked inside the function)
   - Target user exists
   - Cannot deactivate yourself (fail-closed: admin self-suspension is a
     footgun; if you need to demote yourself, do it via direct DB)

## Why SECURITY DEFINER instead of a second RLS policy

A second permissive SELECT policy on users (e.g. `admin_can_read_all`) would OR
with self_only, meaning any user with is_admin=TRUE could read all user rows
via regular queries — but that requires trusting the app layer to check
is_admin before every users query. SECURITY DEFINER is stricter: the access
bypass is inside the database function, not the application code, and the
is_admin check is inside the function body too — so even a SQL injection in the
app can't escalate to cross-user reads without also knowing the function name
and having EXECUTE grant.

## Grants

No new table grants needed — SECURITY DEFINER functions execute with the owner
role's privileges, which already bypasses RLS. The app_user role only needs
EXECUTE on the functions themselves, granted here.

## Downgrade

Drops all three functions. No table schema changes to revert (those are in 012).
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "013_admin_security_definer"
down_revision = "012_admin_role"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"

# ── Function definitions ──────────────────────────────────────────────────────

_CURRENT_USER_IS_ADMIN = sa.text(
    """
CREATE OR REPLACE FUNCTION current_user_is_admin()
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    _uid_raw TEXT;
    _uid UUID;
    _is_admin BOOLEAN;
BEGIN
    -- current_setting('app.user_id', true) is NULL when genuinely unset, but
    -- '' after a pooled connection reverts a prior SET LOCAL on commit --
    -- both mean "no valid user context" and both must fail closed with a
    -- clear error rather than an opaque uuid parse failure on the cast below.
    _uid_raw := current_setting('app.user_id', true);
    IF _uid_raw IS NULL OR _uid_raw = '' THEN
        RAISE EXCEPTION 'app.user_id is not set';
    END IF;
    _uid := _uid_raw::uuid;
    SELECT u.is_admin INTO _is_admin
    FROM users u
    WHERE u.id = _uid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found';
    END IF;
    RETURN _is_admin;
END;
$$;
"""
)

_ADMIN_LIST_USERS = sa.text(
    """
CREATE OR REPLACE FUNCTION admin_list_users()
RETURNS TABLE(
    id UUID,
    email VARCHAR,
    is_admin BOOLEAN,
    active BOOLEAN,
    created_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    _uid_raw TEXT;
    _uid UUID;
    _is_admin BOOLEAN;
BEGIN
    -- Same NULL-or-'' fail-closed guard as current_user_is_admin (see above).
    _uid_raw := current_setting('app.user_id', true);
    IF _uid_raw IS NULL OR _uid_raw = '' THEN
        RAISE EXCEPTION 'app.user_id is not set';
    END IF;
    _uid := _uid_raw::uuid;
    SELECT u.is_admin INTO _is_admin
    FROM users u
    WHERE u.id = _uid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found';
    END IF;
    IF NOT _is_admin THEN
        RAISE EXCEPTION 'access denied: admin role required'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY SELECT u.id, u.email, u.is_admin, u.active, u.created_at
        FROM users u ORDER BY u.created_at;
END;
$$;
"""
)

_ADMIN_SET_USER_ACTIVE = sa.text(
    """
CREATE OR REPLACE FUNCTION admin_set_user_active(
    target_user_id UUID,
    new_active BOOLEAN
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    _uid_raw TEXT;
    _uid UUID;
    _is_admin BOOLEAN;
BEGIN
    -- Same NULL-or-'' fail-closed guard as current_user_is_admin (see above).
    _uid_raw := current_setting('app.user_id', true);
    IF _uid_raw IS NULL OR _uid_raw = '' THEN
        RAISE EXCEPTION 'app.user_id is not set';
    END IF;
    _uid := _uid_raw::uuid;
    SELECT u.is_admin INTO _is_admin
    FROM users u
    WHERE u.id = _uid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found';
    END IF;
    IF NOT _is_admin THEN
        RAISE EXCEPTION 'access denied: admin role required'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF target_user_id = _uid THEN
        RAISE EXCEPTION 'cannot deactivate yourself'
            USING ERRCODE = 'check_violation';
    END IF;
    UPDATE users SET active = new_active WHERE id = target_user_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'target user not found'
            USING ERRCODE = 'no_data_found';
    END IF;
END;
$$;
"""
)

_DROP_CURRENT_USER_IS_ADMIN = sa.text("DROP FUNCTION IF EXISTS current_user_is_admin()")
_DROP_ADMIN_LIST_USERS = sa.text("DROP FUNCTION IF EXISTS admin_list_users()")
_DROP_ADMIN_SET_USER_ACTIVE = sa.text("DROP FUNCTION IF EXISTS admin_set_user_active(UUID, BOOLEAN)")


def upgrade() -> None:
    op.execute(_CURRENT_USER_IS_ADMIN)
    op.execute(_ADMIN_LIST_USERS)
    op.execute(_ADMIN_SET_USER_ACTIVE)

    # Grant EXECUTE to app_user so the API can call these functions.
    op.execute(f"GRANT EXECUTE ON FUNCTION current_user_is_admin() TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION admin_list_users() TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION admin_set_user_active(UUID, BOOLEAN) TO {APP_ROLE}")


def downgrade() -> None:
    # Revoke before drop (clean ordering).
    op.execute(f"REVOKE EXECUTE ON FUNCTION current_user_is_admin() FROM {APP_ROLE}")
    op.execute(f"REVOKE EXECUTE ON FUNCTION admin_list_users() FROM {APP_ROLE}")
    op.execute(f"REVOKE EXECUTE ON FUNCTION admin_set_user_active(UUID, BOOLEAN) FROM {APP_ROLE}")

    op.execute(_DROP_ADMIN_SET_USER_ACTIVE)
    op.execute(_DROP_ADMIN_LIST_USERS)
    op.execute(_DROP_CURRENT_USER_IS_ADMIN)

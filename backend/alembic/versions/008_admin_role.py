"""admin role: users.is_admin/active + SECURITY DEFINER cross-user functions
(account metadata only, no content-table access)

Revision ID: 008_admin_role
Revises: 007_documents_unique_constraint
Create Date: 2026-08-12

Scope decided before this migration was written (see docs/DECISION_LOG.md):
admin grants account-metadata visibility only (email, created_at, is_admin,
active, doc/job counts) -- never document content, findings, or quotes
belonging to another user. This keeps CLAUDE.md rule 1 ("EVERY database
query is scoped by user_id") intact: no RLS policy on documents,
analysis_jobs, analysis_results, or document_summaries changes here, and
none of the new functions ever select a content column.

Design rejected: a second permissive RLS policy on `users`, mirroring
migration 003's email_lookup pattern (`self_only` OR a new admin-check
policy). A self-referential `EXISTS (SELECT ... FROM users ...)` predicate
on a policy attached to `users` itself risks Postgres's own "infinite
recursion detected in policy for relation \"users\"" error, since the
subquery is itself subject to users' RLS. Instead, three SECURITY DEFINER
functions (owned by the migration/owner role, which is superuser+BYPASSRLS
per the 2026-07-15 RLS spike finding) do the cross-user read/write
internally and are the only code path with that visibility -- app_user
itself gets no new grant or policy on `users` beyond EXECUTE on these three
functions.

Design rejected for suspend: widening app_user's UPDATE grant on `users`
plus an admin RLS policy. Migration 001 already grants UPDATE on ALL
columns of users to app_user -- a row-level "admin can UPDATE any row"
policy would let an admin session edit email/password_hash of any user
too, not just `active`, since RLS is row-level, not column-level.
admin_set_user_active's function body touches exactly the `active` column;
that is the entire cross-user write surface, enforced by the function, not
by a grant.

Admin-delete reuses the existing sole owner-role escape hatch,
delete_account_cascade (app/auth.py), called with the target's id -- no
new privileged-connection code path is added here.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "008_admin_role"
down_revision = "007_documents_unique_constraint"
branch_labels = None
depends_on = None

APP_ROLE = "app_user"


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN is_admin boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE users ADD COLUMN active boolean NOT NULL DEFAULT true")
    # No FK, same as the existing doc_id column on this table -- audit_log
    # rows must survive the referenced user being deleted later (CLAUDE.md
    # rule 2's content-free trail outlives the account it describes).
    op.execute("ALTER TABLE audit_log ADD COLUMN target_user_id uuid")

    # SECURITY DEFINER: runs as the function owner (the migration/owner
    # role, superuser+BYPASSRLS), so its internal SELECT/UPDATE never
    # re-triggers users' own RLS policies -- avoids the self-referential-
    # policy recursion problem described in the module docstring.
    op.execute(
        """
        CREATE FUNCTION current_user_is_admin() RETURNS boolean
        LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
          SELECT COALESCE(
            (SELECT is_admin FROM users WHERE id = current_setting('app.user_id')::uuid),
            false
          );
        $$
        """
    )

    # Re-checks admin status itself (defense-in-depth behind require_admin
    # in app code) and only ever selects COUNT(...) from documents/
    # analysis_jobs -- never a content column -- so this cannot leak
    # document text, filenames, or findings regardless of caller.
    op.execute(
        """
        CREATE FUNCTION admin_list_users()
        RETURNS TABLE(id uuid, email text, created_at timestamptz,
                      is_admin boolean, active boolean,
                      document_count bigint, job_count bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
          IF NOT current_user_is_admin() THEN
            RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
          END IF;
          RETURN QUERY
            SELECT u.id, u.email::text, u.created_at, u.is_admin, u.active,
                   COUNT(DISTINCT d.doc_id), COUNT(DISTINCT j.id)
            FROM users u
            LEFT JOIN documents d ON d.user_id = u.id
            LEFT JOIN analysis_jobs j ON j.user_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at;
        END;
        $$
        """
    )

    # The entire cross-user write surface is this one column, enforced by
    # the function body -- not by a grant or an RLS policy (see module
    # docstring's "design rejected for suspend").
    op.execute(
        """
        CREATE FUNCTION admin_set_user_active(target_id uuid, new_active boolean)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE updated_count integer;
        BEGIN
          IF NOT current_user_is_admin() THEN
            RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
          END IF;
          UPDATE users SET active = new_active WHERE id = target_id;
          GET DIAGNOSTICS updated_count = ROW_COUNT;
          RETURN updated_count > 0;
        END;
        $$
        """
    )

    op.execute(f"GRANT EXECUTE ON FUNCTION current_user_is_admin() TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION admin_list_users() TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION admin_set_user_active(uuid, boolean) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS admin_set_user_active(uuid, boolean)")
    op.execute("DROP FUNCTION IF EXISTS admin_list_users()")
    op.execute("DROP FUNCTION IF EXISTS current_user_is_admin()")
    op.execute("ALTER TABLE audit_log DROP COLUMN target_user_id")
    op.execute("ALTER TABLE users DROP COLUMN active")
    op.execute("ALTER TABLE users DROP COLUMN is_admin")

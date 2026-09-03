"""Migration 012 (admin role schema) verification.

Assumes `alembic upgrade head` has already been run against DATABASE_URL — CI
does this in its own step before pytest; locally, run it yourself first (see
backend/tests/conftest.py). Uses the owner engine for schema introspection
and app_user for the RLS verification.

This is PR #1 of 3 — purely additive schema. PR #2 adds SECURITY DEFINER
functions for cross-user admin access; PR #3 adds the /admin/users endpoints.
"""
from __future__ import annotations

import os
import subprocess
import uuid

from sqlalchemy import text

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. Column existence (owner) ──────────────────────────────────────────
def test_users_has_is_admin_column(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'is_admin'"
            )
        ).fetchone()
    assert row is not None, "is_admin column must exist"
    assert row.is_nullable == "NO", "is_admin must be NOT NULL"
    # server_default='false' should show as 'false' or similar
    assert row.column_default is not None and "false" in row.column_default.lower()


def test_users_has_active_column(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'active'"
            )
        ).fetchone()
    assert row is not None, "active column must exist"
    assert row.is_nullable == "NO", "active must be NOT NULL"
    # server_default='true' should show as 'true' or similar
    assert row.column_default is not None and "true" in row.column_default.lower()


def test_audit_log_has_target_user_id_column(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT is_nullable, data_type "
                "FROM information_schema.columns "
                "WHERE table_name = 'audit_log' AND column_name = 'target_user_id'"
            )
        ).fetchone()
        assert row is not None, "target_user_id column must exist"
        assert row.is_nullable == "YES", "target_user_id must be NULLABLE"

        # Verify no FK constraint exists (matches doc_id precedent)
        fk_exists = conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = 'audit_log'::regclass AND contype = 'f' "
                "AND conname LIKE '%target_user_id%'"
            )
        ).scalar()
        assert fk_exists is None, "target_user_id must have no FK constraint"


# ── 2. Defaults for existing rows (owner) ─────────────────────────────────
def test_existing_users_get_correct_defaults(pg_owner_engine):
    """Insert a user as owner (bypasses RLS), verify defaults are applied."""
    user_id = uuid.uuid4()
    try:
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at) "
                    "VALUES (:id, :email, 'x', now())"
                ),
                {"id": user_id, "email": f"{user_id}@example.invalid"},
            )
            conn.commit()

        with pg_owner_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_admin, active FROM users WHERE id = :id"
                ),
                {"id": user_id},
            ).fetchone()
        assert row is not None
        assert row.is_admin is False, "new user must have is_admin=FALSE by default"
        assert row.active is True, "new user must have active=TRUE by default"
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            conn.commit()


# ── 3. RLS still enforced (app_user) ──────────────────────────────────────
def test_users_rls_still_enforced(pg_app_engine, pg_owner_engine):
    """Verify FORCE RLS on users is still working after adding new columns.

    Since migration 003, users has a permissive SELECT policy (email_lookup,
    USING true) for pre-auth login-by-email. Permissive policies OR together,
    so an unscoped SELECT by app_user isn't filtered — self_only only
    constrains INSERT/UPDATE. Real app code scopes by id = current_user_id.

    This test verifies self_only by trying an UPDATE on another user's row —
    it must fail, proving RLS still enforces self-row-only for mutations.
    """
    # Create two users via owner (bypasses RLS)
    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()
    try:
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at) "
                    "VALUES (:id, :email, 'x', now())"
                ),
                {"id": user_a_id, "email": f"{user_a_id}@a.invalid"},
            )
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at) "
                    "VALUES (:id, :email, 'x', now())"
                ),
                {"id": user_b_id, "email": f"{user_b_id}@b.invalid"},
            )
            conn.commit()

        # app_user with user A's ID tries to UPDATE user B's row → must fail (self_only enforced)
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{user_a_id}'"))
            result = conn.execute(
                text("UPDATE users SET email = 'hacked@example.invalid' WHERE id = :id"),
                {"id": user_b_id}
            )
            conn.rollback()

        # UPDATE returned 0 rows (RLS blocked it) — not an error, just no match
        assert result.rowcount == 0, "user A must NOT be able to UPDATE user B's row (RLS enforced)"
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_a_id})
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_b_id})
            conn.commit()


# ── 4. Downgrade/upgrade (owner) ──────────────────────────────────────────
def test_downgrade_removes_columns(pg_owner_engine):
    """Downgrade to 011, verify columns are gone, re-upgrade, verify they're back."""

    def _run(*args: str) -> None:
        subprocess.run(
            ["python", "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=os.environ,
            check=True,
            capture_output=True,
            text=True,
        )

    def _is_admin_exists() -> bool:
        with pg_owner_engine.connect() as conn:
            return bool(
                conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name = 'is_admin'"
                    )
                ).scalar()
            )

    def _active_exists() -> bool:
        with pg_owner_engine.connect() as conn:
            return bool(
                conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name = 'active'"
                    )
                ).scalar()
            )

    def _target_user_id_exists() -> bool:
        with pg_owner_engine.connect() as conn:
            return bool(
                conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'audit_log' AND column_name = 'target_user_id'"
                    )
                ).scalar()
            )

    try:
        # Downgrade
        _run("downgrade", "011_outbox_relay")
        assert not _is_admin_exists(), "is_admin column must be gone after downgrade"
        assert not _active_exists(), "active column must be gone after downgrade"
        assert not _target_user_id_exists(), "target_user_id column must be gone after downgrade"
    finally:
        # Re-upgrade to head
        _run("upgrade", "head")

    assert _is_admin_exists(), "is_admin column must be back after upgrade"
    assert _active_exists(), "active column must be back after upgrade"
    assert _target_user_id_exists(), "target_user_id column must be back after upgrade"
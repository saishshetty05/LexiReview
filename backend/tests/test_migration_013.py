"""Migration 013 (SECURITY DEFINER admin functions) verification.

Assumes `alembic upgrade head` has already been run against DATABASE_URL — CI
does this in its own step before pytest; locally, run it yourself first (see
backend/tests/conftest.py). Uses the owner engine for schema introspection and
seeding users, and app_user for the function behavior checks (SECURITY DEFINER
means the function body runs as its owner, so app_user can call it and still
get cross-user access — that's exactly the design being verified here).

These functions are PR #2 of 3. PR #3 adds the /admin/users endpoints that call
them; this file validates the DB layer in isolation.

Key invariant under test: each function re-checks the calling user's is_admin
*inside* the function body (via current_setting('app.user_id')), so a non-admin
who somehow gets EXECUTE on the function gets a clear error, not silent
cross-user reads.
"""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from sqlalchemy import text

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Helpers ────────────────────────────────────────────────────────────────
def _seed_user(pg_owner_engine, *, is_admin: bool, active: bool = True) -> uuid.UUID:
    """Insert a user directly via owner (bypasses RLS). Returns the user id."""
    user_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_admin, active, created_at) "
                "VALUES (:id, :email, 'x', :is_admin, :active, now())"
            ),
            {
                "id": user_id,
                "email": f"{user_id}@example.invalid",
                "is_admin": is_admin,
                "active": active,
            },
        )
        conn.commit()
    return user_id


def _cleanup_users(pg_owner_engine, *user_ids: uuid.UUID) -> None:
    with pg_owner_engine.connect() as conn:
        for uid in user_ids:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
        conn.commit()


def _call_as(engine, user_id, sql: str) -> object:
    """Run `sql` in a transaction where app.user_id = user_id (simulating that
    user making the call), returning the result value. Commits so SET LOCAL
    doesn't leak.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        result = conn.execute(text(sql))
        conn.commit()
        return result


# ── 1. Function existence (owner) ──────────────────────────────────────────
def test_three_functions_exist(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT proname, prosecdef, proowner::regrole::text "
                "FROM pg_proc JOIN pg_namespace n ON n.oid = pronamespace "
                "WHERE n.nspname = 'public' "
                "AND proname IN ('current_user_is_admin', 'admin_list_users', 'admin_set_user_active')"
            )
        ).fetchall()
    names = {r[0] for r in rows}
    assert names == {
        "current_user_is_admin",
        "admin_list_users",
        "admin_set_user_active",
    }, f"missing functions, got: {names}"
    # Every function must be SECURITY DEFINER (prosecdef).
    assert all(r.prosecdef for r in rows), "all three functions must be SECURITY DEFINER"


# ── 2. current_user_is_admin() (app_user) ──────────────────────────────────
def test_current_user_is_admin_returns_false_for_non_admin(pg_owner_engine, pg_app_engine):
    non_admin = _seed_user(pg_owner_engine, is_admin=False)
    admin = _seed_user(pg_owner_engine, is_admin=True)
    try:
        # Non-admin calling as themselves → FALSE.
        (is_admin,) = _call_as(pg_app_engine, non_admin, "SELECT current_user_is_admin()").fetchone()
        assert is_admin is False
        # Admin calling as themselves → TRUE.
        (is_admin,) = _call_as(pg_app_engine, admin, "SELECT current_user_is_admin()").fetchone()
        assert is_admin is True
    finally:
        _cleanup_users(pg_owner_engine, non_admin, admin)


def test_current_user_is_admin_raises_when_app_user_id_unset(pg_app_engine):
    """Fail-closed: a session with no app.user_id must not silently report
    "is admin". The function reads current_setting('app.user_id', true) which
    returns NULL rather than erroring, and we RAISE EXCEPTION on NULL, so an
    unsetset session is rejected rather than interpreted as a valid (non-admin)
    caller.
    """
    with pg_app_engine.connect() as conn:
        # No SET LOCAL app.user_id on purpose.
        with pytest.raises(Exception, match="app.user_id"):
            conn.execute(text("SELECT current_user_is_admin()"))
        conn.rollback()


# ── 3. admin_list_users() (app_user) ───────────────────────────────────────
def test_admin_list_users_rejects_non_admin(pg_owner_engine, pg_app_engine):
    non_admin = _seed_user(pg_owner_engine, is_admin=False)
    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{non_admin}'"))
            with pytest.raises(Exception, match="admin role required"):
                conn.execute(text("SELECT * FROM admin_list_users()"))
            conn.rollback()
    finally:
        _cleanup_users(pg_owner_engine, non_admin)


def test_admin_list_users_returns_all_users(pg_owner_engine, pg_app_engine):
    admin = _seed_user(pg_owner_engine, is_admin=True)
    target_a = _seed_user(pg_owner_engine, is_admin=False)
    target_b = _seed_user(pg_owner_engine, is_admin=False, active=False)
    try:
        # Admin calls admin_list_users → sees every user (cross-user bypass).
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{admin}'"))
            rows = conn.execute(
                text("SELECT id::text, is_admin, active FROM admin_list_users()")
            ).fetchall()
            conn.commit()
        seen_ids = {r[0] for r in rows}
        assert str(admin) in seen_ids
        assert str(target_a) in seen_ids
        assert str(target_b) in seen_ids

        # Rowshape sanity: is_admin/active columns ride through.
        by_id = {r[0]: r for r in rows}
        assert by_id[str(admin)].is_admin is True
        assert by_id[str(target_b)].active is False
    finally:
        _cleanup_users(pg_owner_engine, admin, target_a, target_b)


# ── 4. admin_set_user_active() (app_user) ──────────────────────────────────
def test_admin_set_user_active_suspends_target(pg_owner_engine, pg_app_engine):
    admin = _seed_user(pg_owner_engine, is_admin=True)
    target = _seed_user(pg_owner_engine, is_admin=False)
    try:
        _call_as(pg_app_engine, admin, f"SELECT admin_set_user_active('{target}', false)")
        with pg_owner_engine.connect() as conn:
            (active,) = conn.execute(
                text("SELECT active FROM users WHERE id = :id"), {"id": target}
            ).fetchone()
        assert active is False, "admin must be able to suspend a target user"

        _call_as(pg_app_engine, admin, f"SELECT admin_set_user_active('{target}', true)")
        with pg_owner_engine.connect() as conn:
            (active,) = conn.execute(
                text("SELECT active FROM users WHERE id = :id"), {"id": target}
            ).fetchone()
        assert active is True, "admin must be able to reactivate a target user"
    finally:
        _cleanup_users(pg_owner_engine, admin, target)


def test_admin_set_user_active_rejects_non_admin(pg_owner_engine, pg_app_engine):
    non_admin = _seed_user(pg_owner_engine, is_admin=False)
    target = _seed_user(pg_owner_engine, is_admin=False)
    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{non_admin}'"))
            with pytest.raises(Exception, match="admin role required"):
                conn.execute(
                    text(f"SELECT admin_set_user_active('{target}', false)")
                )
            conn.rollback()
    finally:
        _cleanup_users(pg_owner_engine, non_admin, target)


def test_admin_set_user_active_cannot_self_suspend(pg_owner_engine, pg_app_engine):
    admin = _seed_user(pg_owner_engine, is_admin=True)
    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{admin}'"))
            with pytest.raises(Exception, match="cannot deactivate yourself"):
                conn.execute(
                    text(f"SELECT admin_set_user_active('{admin}', false)")
                )
            conn.rollback()
        # Admin still active.
        with pg_owner_engine.connect() as conn:
            (active,) = conn.execute(
                text("SELECT active FROM users WHERE id = :id"), {"id": admin}
            ).fetchone()
        assert active is True
    finally:
        _cleanup_users(pg_owner_engine, admin)


def test_admin_set_user_active_missing_target_is_error(pg_owner_engine, pg_app_engine):
    admin = _seed_user(pg_owner_engine, is_admin=True)
    missing = uuid.uuid4()
    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{admin}'"))
            with pytest.raises(Exception):
                conn.execute(
                    text(f"SELECT admin_set_user_active('{missing}', false)")
                )
            conn.rollback()
    finally:
        _cleanup_users(pg_owner_engine, admin)


# ── 5. EXECUTE grant (owner) ──────────────────────────────────────────────
def test_app_user_has_execute_grant(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT p.proname
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname IN ('current_user_is_admin', 'admin_list_users', 'admin_set_user_active')
                  AND has_function_privilege('app_user', p.oid, 'EXECUTE')
            """)
        ).fetchall()
    assert {r[0] for r in rows} == {
        "current_user_is_admin",
        "admin_list_users",
        "admin_set_user_active",
    }, "app_user must have EXECUTE on all three functions"


# ── 6. Downgrade/upgrade (owner) ───────────────────────────────────────────
def test_downgrade_removes_functions_and_reupgrade(pg_owner_engine):
    def _run(*args: str) -> None:
        subprocess.run(
            ["python", "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=os.environ,
            check=True,
            capture_output=True,
            text=True,
        )

    def _func_names() -> set[str]:
        with pg_owner_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT proname FROM pg_proc JOIN pg_namespace n ON n.oid = pronamespace "
                    "WHERE n.nspname = 'public' "
                    "AND proname IN ('current_user_is_admin', 'admin_list_users', 'admin_set_user_active')"
                )
            ).fetchall()
        return {r[0] for r in rows}

    try:
        _run("downgrade", "012_admin_role")
        assert _func_names() == set(), "functions must be gone after downgrade"
    finally:
        _run("upgrade", "head")

    assert _func_names() == {
        "current_user_is_admin",
        "admin_list_users",
        "admin_set_user_active",
    }, "functions must be back after upgrade"

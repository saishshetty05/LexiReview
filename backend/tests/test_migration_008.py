"""Verifies migration 008 (admin role: users.is_admin/active, audit_log.
target_user_id, and the three SECURITY DEFINER cross-user functions) --
applies + downgrades cleanly, new columns default correctly, and the
functions enforce admin-only access themselves (defense-in-depth behind
app code's require_admin dependency, per the migration's own docstring).
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _admin_columns_exist(pg_owner_engine) -> bool:
    with pg_owner_engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name IN ('is_admin', 'active')"
                )
            ).fetchall()
        }
    return cols == {"is_admin", "active"}


def test_migration_008_applies_and_downgrades_cleanly(pg_owner_engine):
    """Assumes the test DB starts at head (008). Downgrades to 007, checks
    the new columns/functions are gone, then re-upgrades to head and checks
    they're back -- leaving the DB at head for every other test in the
    suite, same pattern as test_migration_004.py/test_migration_005.py.
    """

    def _run(*args: str) -> None:
        subprocess.run(
            ["python", "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=os.environ,
            check=True,
            capture_output=True,
            text=True,
        )

    assert _admin_columns_exist(pg_owner_engine)

    try:
        _run("downgrade", "007_documents_unique_constraint")
        assert not _admin_columns_exist(pg_owner_engine)
        with pg_owner_engine.connect() as conn:
            target_user_id_gone = not conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'audit_log' AND column_name = 'target_user_id'"
                )
            ).fetchall()
        assert target_user_id_gone
    finally:
        _run("upgrade", "head")

    assert _admin_columns_exist(pg_owner_engine)


def test_is_admin_and_active_default_correctly(pg_owner_engine):
    user_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
        conn.commit()

        is_admin, active = conn.execute(
            text("SELECT is_admin, active FROM users WHERE id = :id"), {"id": user_id}
        ).one()
        assert is_admin is False
        assert active is True

        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        conn.commit()


def _make_user(conn, *, is_admin: bool = False) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, password_hash, is_admin, created_at) "
            "VALUES (:id, :email, 'x', :is_admin, now())"
        ),
        {"id": user_id, "email": f"{user_id}@example.invalid", "is_admin": is_admin},
    )
    return user_id


def test_admin_list_users_rejects_non_admin_caller(pg_app_engine, pg_owner_engine):
    with pg_owner_engine.connect() as owner_conn:
        non_admin_id = _make_user(owner_conn)
        owner_conn.commit()

    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{non_admin_id}'"))
            with pytest.raises(ProgrammingError, match="not authorized"):
                conn.execute(text("SELECT * FROM admin_list_users()"))
            conn.rollback()
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": non_admin_id})
            conn.commit()


def test_admin_list_users_returns_counts_for_admin_caller(pg_app_engine, pg_owner_engine):
    with pg_owner_engine.connect() as owner_conn:
        admin_id = _make_user(owner_conn, is_admin=True)
        other_id = _make_user(owner_conn)
        owner_conn.commit()

    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{admin_id}'"))
            rows = conn.execute(
                text("SELECT id, document_count, job_count FROM admin_list_users()")
            ).fetchall()
            conn.rollback()

        seen_ids = {r[0] for r in rows}
        assert {admin_id, other_id} <= seen_ids
        counts_by_id = {r[0]: (r[1], r[2]) for r in rows}
        assert counts_by_id[other_id] == (0, 0)
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [admin_id, other_id]}
            )
            conn.commit()


def test_admin_set_user_active_rejects_non_admin_caller(pg_app_engine, pg_owner_engine):
    with pg_owner_engine.connect() as owner_conn:
        non_admin_id = _make_user(owner_conn)
        target_id = _make_user(owner_conn)
        owner_conn.commit()

    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{non_admin_id}'"))
            with pytest.raises(ProgrammingError, match="not authorized"):
                conn.execute(
                    text("SELECT admin_set_user_active(:target, false)"), {"target": target_id}
                )
            conn.rollback()

        with pg_owner_engine.connect() as conn:
            active = conn.execute(
                text("SELECT active FROM users WHERE id = :id"), {"id": target_id}
            ).scalar_one()
        assert active is True
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [non_admin_id, target_id]}
            )
            conn.commit()


def test_admin_set_user_active_flips_exactly_the_active_column(pg_app_engine, pg_owner_engine):
    with pg_owner_engine.connect() as owner_conn:
        admin_id = _make_user(owner_conn, is_admin=True)
        target_id = _make_user(owner_conn)
        owner_conn.commit()
        target_email_before = owner_conn.execute(
            text("SELECT email FROM users WHERE id = :id"), {"id": target_id}
        ).scalar_one()

    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{admin_id}'"))
            result = conn.execute(
                text("SELECT admin_set_user_active(:target, false)"), {"target": target_id}
            ).scalar_one()
            conn.commit()
        assert result is True

        with pg_owner_engine.connect() as owner_conn:
            active, email_after = owner_conn.execute(
                text("SELECT active, email FROM users WHERE id = :id"), {"id": target_id}
            ).one()
        assert active is False
        assert email_after == target_email_before  # only `active` moved

        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{admin_id}'"))
            reactivate_result = conn.execute(
                text("SELECT admin_set_user_active(:target, true)"), {"target": target_id}
            ).scalar_one()
            conn.commit()
        assert reactivate_result is True
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [admin_id, target_id]}
            )
            conn.commit()


def test_admin_set_user_active_returns_false_for_unknown_target(pg_app_engine, pg_owner_engine):
    with pg_owner_engine.connect() as owner_conn:
        admin_id = _make_user(owner_conn, is_admin=True)
        owner_conn.commit()

    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{admin_id}'"))
            result = conn.execute(
                text("SELECT admin_set_user_active(:target, false)"), {"target": uuid.uuid4()}
            ).scalar_one()
            conn.rollback()
        assert result is False
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": admin_id})
            conn.commit()

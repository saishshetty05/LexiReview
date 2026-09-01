"""Tests for migration 011 (outbox table + dedicated relay role).

Verifies the schema half of CONTRACTS.md §1/§5 (v1.13): the `outbox` table,
the non-superuser/non-BYPASSRLS `relay` role, grants-only access control
(deliberately NO RLS — see the migration 011 docstring), and the ON DELETE
CASCADE from outbox.job_id → analysis_jobs.id.

Assumes `alembic upgrade head` (migration 011) has already been run against
DATABASE_URL — CI does this in its own step before pytest; locally, run it
yourself first with RELAY_USER_PASSWORD set (see backend/tests/conftest.py).
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

OUTBOX_COLUMNS = {
    "id",
    "job_id",
    "payload_json",
    "attempts",
    "last_attempt_at",
    "delivered_at",
    "created_at",
}

# Every user-data table the relay must NOT be able to touch (least-privilege).
USER_TABLES = {
    "users",
    "documents",
    "analysis_jobs",
    "analysis_results",
    "audit_log",
    "document_summaries",
    "decisions",
    "refresh_tokens",
}


def _seed_user_and_job(owner_engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a user + analysis_jobs row and return (user_id, job_id)."""
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'hash', now())"
            ),
            {"id": user_id, "email": f"m011-{user_id}@example.invalid"},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, created_at) "
                "VALUES (:id, :uid, :doc_id, 'hash1', 'queued', now())"
            ),
            {"id": job_id, "uid": user_id, "doc_id": doc_id},
        )
    return user_id, job_id


def test_outbox_table_columns_and_constraints(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        cols = {
            c
            for c in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'outbox'"
                )
            ).scalars()
        }
        attempts_default = conn.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'outbox' AND column_name = 'attempts'"
            )
        ).scalar()
        job_id_nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'outbox' AND column_name = 'job_id'"
            )
        ).scalar()
        fk = conn.execute(
            text(
                "SELECT f.confdeltype "
                "FROM pg_constraint f "
                "JOIN pg_class r ON r.oid = f.conrelid "
                "WHERE r.relname = 'outbox' AND f.contype = 'f'"
            )
        ).scalar()
        uniq_count = conn.execute(
            text(
                "SELECT count(*) FROM pg_constraint c "
                "JOIN pg_class r ON r.oid = c.conrelid "
                "WHERE r.relname = 'outbox' AND c.contype = 'u'"
            )
        ).scalar()

    assert cols == OUTBOX_COLUMNS
    assert attempts_default == "0"
    assert job_id_nullable == "NO"
    # 'c' = ON DELETE CASCADE — account deletion removes job -> outbox follows.
    assert fk == "c"
    # One outbox row per analysis job.
    assert uniq_count == 1


def test_outbox_has_no_rls_by_design(pg_owner_engine):
    """The outbox is the ONE table that intentionally skips FORCE RLS — the
    relay sweeps cross-user, which RLS (user-scoped) cannot express. Access is
    grants-only. Locks it in so a future dev doesn't "fix" it into FORCE RLS
    and silently break the relay.
    """
    with pg_owner_engine.connect() as conn:
        rls_enabled, rls_forced = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = 'outbox'"
            )
        ).fetchone()
    assert rls_enabled is False
    assert rls_forced is False


def test_relay_role_is_non_superuser_no_bypassrls(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'relay'")
        ).fetchone()
    assert row is not None, "relay role was not created by migration 011"
    rolsuper, rolbypassrls = row
    assert rolsuper is False, "relay must not be superuser (least-privilege)"
    assert rolbypassrls is False, "relay must not have BYPASSRLS (least-privilege)"


def test_outbox_grants_and_relay_no_user_table_access(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT grantee, table_name, privilege_type "
                "FROM information_schema.role_table_grants "
                "WHERE grantee IN ('app_user', 'relay')"
            )
        ).fetchall()

    app_outbox = {priv for g, t, priv in rows if g == "app_user" and t == "outbox"}
    relay_outbox = {priv for g, t, priv in rows if g == "relay" and t == "outbox"}
    relay_other_tables = {t for g, t, _ in rows if g == "relay" and t != "outbox"}

    # app_user: INSERT only (the upload writes the row; the API never reads it).
    assert app_outbox == {"INSERT"}
    # relay: SELECT+UPDATE (discover + stamp attempts/delivered_at), no insert/delete.
    assert relay_outbox == {"SELECT", "UPDATE"}
    # A compromised relay must be able to touch NOTHING except the outbox.
    assert relay_other_tables == set()
    assert not (USER_TABLES & relay_other_tables)


def test_relay_cross_user_sweep_and_scoping(pg_owner_engine, pg_app_engine, pg_relay_engine):
    """End-to-end access model: app_user inserts the outbox row (upload path);
    relay reads it with no user context and stamps it; and both roles are
    sandboxed — relay can't read documents, app_user can't read the outbox.
    """
    user_id, job_id = _seed_user_and_job(pg_owner_engine)
    outbox_id = uuid.uuid4()
    try:
        # The upload path: app_user writes the outbox row in its own session.
        with pg_app_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO outbox (id, job_id, payload_json, attempts, created_at) "
                    "VALUES (:id, :job, CAST(:payload AS jsonb), 0, now())"
                ),
                {
                    "id": outbox_id,
                    "job": job_id,
                    "payload": json.dumps(
                        {"task": "analyze_document", "args": [str(job_id), str(user_id)]}
                    ),
                },
            )

        # The relay: no user context at all (no SET LOCAL app.user_id), and yet
        # it sees the row — grants-only, no RLS gate. This is the cross-user sweep.
        with pg_relay_engine.begin() as conn:
            seen = conn.execute(
                text("SELECT count(*) FROM outbox WHERE id = :id"), {"id": outbox_id}
            ).scalar()
            assert seen == 1
            conn.execute(
                text("UPDATE outbox SET attempts = attempts + 1, delivered_at = now() WHERE id = :id"),
                {"id": outbox_id},
            )

        # Stamps persisted (owner can read; app has no SELECT on outbox).
        with pg_owner_engine.connect() as conn:
            attempts, delivered_at = conn.execute(
                text("SELECT attempts, delivered_at FROM outbox WHERE id = :id"),
                {"id": outbox_id},
            ).fetchone()
        assert attempts == 1
        assert delivered_at is not None

        # Relay is sandboxed from user data.
        with pg_relay_engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(text("SELECT id FROM documents")).fetchall()

        # And app_user cannot read the outbox back.
        with pg_app_engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(text("SELECT id FROM outbox")).fetchall()
    finally:
        with pg_owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM analysis_jobs WHERE user_id = :uid"), {"uid": user_id})
            conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})


def test_outbox_row_cascades_when_job_deleted(pg_owner_engine):
    """ON DELETE CASCADE (outbox.job_id → analysis_jobs.id): deleting a job —
    which is what delete_account_cascade does — removes the outbox row, so the
    relay never attempts delivery for a deleted user's job.
    """
    user_id, job_id = _seed_user_and_job(pg_owner_engine)
    outbox_id = uuid.uuid4()
    try:
        with pg_owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO outbox (id, job_id, payload_json, created_at) "
                    "VALUES (:id, :job, '{}'::jsonb, now())"
                ),
                {"id": outbox_id, "job": job_id},
            )

        with pg_owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM analysis_jobs WHERE id = :job"), {"job": job_id})

        with pg_owner_engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT count(*) FROM outbox WHERE id = :id"), {"id": outbox_id}
            ).scalar()
        assert remaining == 0
    finally:
        with pg_owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM analysis_jobs WHERE user_id = :uid"), {"uid": user_id})
            conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})

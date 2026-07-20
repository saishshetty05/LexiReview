"""Verifies migration 004 (analysis_jobs.summary_error, CONTRACTS.md §2c):
applies + downgrades cleanly, the column is nullable, and RLS still scopes
analysis_jobs correctly with the new column present.

Assumes `alembic upgrade head` has already been run against DATABASE_URL --
CI does this in its own step before pytest; locally, run it yourself first
(see backend/tests/conftest.py).
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _summary_error_column_exists(pg_owner_engine) -> bool:
    with pg_owner_engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'analysis_jobs' AND column_name = 'summary_error'"
                )
            ).scalar()
        )


def test_migration_004_applies_and_downgrades_cleanly(pg_owner_engine):
    """Assumes the test DB starts at head (004). Downgrades to 003, checks
    summary_error is gone, then re-upgrades to head and checks it's back --
    leaving the DB at head for every other test in the suite, same pattern
    as test_migration_apply.py's migration-002 test.
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

    assert _summary_error_column_exists(pg_owner_engine)

    try:
        _run("downgrade", "003_users_email_lookup")
        assert not _summary_error_column_exists(pg_owner_engine)
    finally:
        _run("upgrade", "head")

    assert _summary_error_column_exists(pg_owner_engine)


def test_summary_error_is_nullable(pg_owner_engine):
    user_id, doc_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, created_at) "
                "VALUES (:id, :uid, :doc_id, 'testhash', 'queued', now())"
            ),
            {"id": job_id, "uid": user_id, "doc_id": doc_id},
        )
        conn.commit()

        summary_error = conn.execute(
            text("SELECT summary_error FROM analysis_jobs WHERE id = :id"), {"id": job_id}
        ).scalar_one()
        assert summary_error is None

        conn.execute(text("DELETE FROM analysis_jobs WHERE id = :id"), {"id": job_id})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        conn.commit()


def test_rls_still_scopes_analysis_jobs_with_summary_error_present(pg_app_engine, pg_owner_engine):
    """Same two-user isolation proof as test_rls_smoke.py, re-run post-migration
    to confirm the new column doesn't disturb the existing user_isolation
    policy -- ALTER TABLE ADD COLUMN doesn't touch existing RLS policies,
    but this is the explicit, testable confirmation rather than an assumption.
    """
    user1, user2 = uuid.uuid4(), uuid.uuid4()
    job1, job2 = uuid.uuid4(), uuid.uuid4()

    with pg_app_engine.connect() as conn:
        for uid, jid in ((user1, job1), (user2, job2)):
            conn.execute(text(f"SET LOCAL app.user_id = '{uid}'"))
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at) "
                    "VALUES (:id, :email, 'x', now())"
                ),
                {"id": uid, "email": f"{uid}@example.invalid"},
            )
            conn.execute(
                text(
                    "INSERT INTO analysis_jobs "
                    "(id, user_id, doc_id, doc_version_hash, state, summary_error, created_at) "
                    "VALUES (:id, :uid, :doc_id, 'testhash', 'queued', 'llm_error: rate limited', now())"
                ),
                {"id": jid, "uid": uid, "doc_id": uuid.uuid4()},
            )
            conn.commit()

    try:
        with pg_app_engine.connect() as conn:
            conn.execute(text(f"SET LOCAL app.user_id = '{user1}'"))
            rows = conn.execute(
                text("SELECT id, summary_error FROM analysis_jobs")
            ).fetchall()
            conn.rollback()

        seen_ids = {r[0] for r in rows}
        assert seen_ids == {job1}
        assert job2 not in seen_ids
        assert rows[0][1] == "llm_error: rate limited"
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text("DELETE FROM analysis_jobs WHERE user_id = ANY(:ids)"),
                {"ids": [user1, user2]},
            )
            conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [user1, user2]})
            conn.commit()

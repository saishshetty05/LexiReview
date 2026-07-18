"""RLS smoke test against the real, migrated analysis_jobs table — the
pytest version of backend/spikes/rls_spike.py's isolation proof, run as
app_user (non-superuser, RLS-enforced) via a pooled connection, matching how
the api/worker containers actually connect.

Assumes `alembic upgrade head` has already been run against DATABASE_URL —
CI does this in its own step before pytest; locally, run it yourself first
(see backend/tests/conftest.py).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError


def _insert_user_and_jobs(app_engine, *, user_id: uuid.UUID, job_count: int) -> list[uuid.UUID]:
    """Insert one users row + N analysis_jobs rows, all scoped to user_id, as
    app_user under RLS. Mirrors real write patterns: SET LOCAL app.user_id,
    then INSERT — the user_isolation/self_only policies' implicit WITH CHECK
    (same as USING, since none is given explicitly) requires the new row's
    id/user_id to match the currently set app.user_id.
    """
    job_ids = [uuid.uuid4() for _ in range(job_count)]
    with app_engine.connect() as conn:
        # SET LOCAL, like CREATE ROLE, is DDL/config -- Postgres rejects bind
        # parameters there ("syntax error at or near $1"). uuid.UUID's str()
        # is safe to inline directly (hex digits and hyphens only).
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
        for job_id in job_ids:
            conn.execute(
                text(
                    "INSERT INTO analysis_jobs "
                    "(id, user_id, doc_id, doc_version_hash, state, created_at) "
                    "VALUES (:id, :uid, :doc_id, 'testhash', 'queued', now())"
                ),
                {"id": job_id, "uid": user_id, "doc_id": uuid.uuid4()},
            )
        conn.commit()
    return job_ids


@pytest.fixture()
def two_users_with_jobs(pg_app_engine, pg_owner_engine):
    user1, user2 = uuid.uuid4(), uuid.uuid4()
    user1_jobs = _insert_user_and_jobs(pg_app_engine, user_id=user1, job_count=2)
    user2_jobs = _insert_user_and_jobs(pg_app_engine, user_id=user2, job_count=2)
    yield user1, user2, user1_jobs, user2_jobs
    # Cleanup runs via the owner role — app_user has no DELETE grant by
    # design (docs/DECISION_LOG.md 2026-07-15).
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM analysis_jobs WHERE user_id = ANY(:ids)"),
            {"ids": [user1, user2]},
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [user1, user2]})
        conn.commit()


def test_scoped_access_sees_only_own_jobs(pg_app_engine, two_users_with_jobs):
    """SET LOCAL app.user_id = user1 -> expect only user1's 2 rows."""
    user1, _user2, user1_jobs, _user2_jobs = two_users_with_jobs

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user1}'"))
        rows = conn.execute(text("SELECT id, user_id FROM analysis_jobs")).fetchall()
        conn.rollback()

    seen_ids = {r[0] for r in rows}
    seen_users = {r[1] for r in rows}
    assert seen_ids == set(user1_jobs)
    assert seen_users == {user1}


def test_scoped_access_does_not_leak_other_users_jobs(pg_app_engine, two_users_with_jobs):
    """The flip side of the above: user2's rows must never appear for user1."""
    user1, _user2, _user1_jobs, user2_jobs = two_users_with_jobs

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user1}'"))
        rows = conn.execute(text("SELECT id FROM analysis_jobs")).fetchall()
        conn.rollback()

    seen_ids = {r[0] for r in rows}
    assert seen_ids.isdisjoint(set(user2_jobs))


def test_unscoped_access_raises_instead_of_leaking(pg_app_engine, two_users_with_jobs):
    """A pooled connection with no app.user_id set gets a hard DB error, not
    a silent empty result. The policy predicate casts current_setting(...)
    to ::uuid; with the session variable unset, current_setting(..., true)
    returns '' and ''::uuid raises InvalidTextRepresentation. This is a
    DELIBERATE fail-closed choice (2026-07-16 decision): a forgotten
    SET LOCAL app.user_id becomes a loud, impossible-to-miss error rather
    than a query that quietly returns zero rows. No data ever leaks either
    way, but an unset session variable is a bug the app should surface, not
    swallow.
    """
    with pg_app_engine.connect() as conn:
        with pytest.raises(DataError, match="invalid input syntax for type uuid"):
            conn.execute(text("SELECT id FROM analysis_jobs"))
        conn.rollback()


def _insert_user_and_summaries(
    app_engine, *, user_id: uuid.UUID, summary_count: int
) -> list[uuid.UUID]:
    """Same pattern as _insert_user_and_jobs, for document_summaries
    (CONTRACTS.md §2b / migration 002).
    """
    summary_ids = [uuid.uuid4() for _ in range(summary_count)]
    with app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
        for summary_id in summary_ids:
            conn.execute(
                text(
                    "INSERT INTO document_summaries "
                    "(id, user_id, doc_id, doc_version_hash, model_version, payload, created_at) "
                    "VALUES (:id, :uid, :doc_id, :hash, 'test-model-v1', '{}'::jsonb, now())"
                ),
                {
                    "id": summary_id,
                    "uid": user_id,
                    "doc_id": uuid.uuid4(),
                    "hash": f"testhash-{summary_id}",
                },
            )
        conn.commit()
    return summary_ids


@pytest.fixture()
def two_users_with_summaries(pg_app_engine, pg_owner_engine):
    user1, user2 = uuid.uuid4(), uuid.uuid4()
    user1_summaries = _insert_user_and_summaries(pg_app_engine, user_id=user1, summary_count=2)
    user2_summaries = _insert_user_and_summaries(pg_app_engine, user_id=user2, summary_count=2)
    yield user1, user2, user1_summaries, user2_summaries
    # Cleanup runs via the owner role — app_user has no DELETE grant by
    # design (docs/DECISION_LOG.md 2026-07-15, migration 002 follows suit).
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM document_summaries WHERE user_id = ANY(:ids)"),
            {"ids": [user1, user2]},
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [user1, user2]})
        conn.commit()


def test_document_summaries_scoped_access_sees_only_own_rows(
    pg_app_engine, two_users_with_summaries
):
    user1, _user2, user1_summaries, _user2_summaries = two_users_with_summaries

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user1}'"))
        rows = conn.execute(text("SELECT id, user_id FROM document_summaries")).fetchall()
        conn.rollback()

    seen_ids = {r[0] for r in rows}
    seen_users = {r[1] for r in rows}
    assert seen_ids == set(user1_summaries)
    assert seen_users == {user1}


def test_document_summaries_scoped_access_does_not_leak_other_users_rows(
    pg_app_engine, two_users_with_summaries
):
    user1, _user2, _user1_summaries, user2_summaries = two_users_with_summaries

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user1}'"))
        rows = conn.execute(text("SELECT id FROM document_summaries")).fetchall()
        conn.rollback()

    seen_ids = {r[0] for r in rows}
    assert seen_ids.isdisjoint(set(user2_summaries))

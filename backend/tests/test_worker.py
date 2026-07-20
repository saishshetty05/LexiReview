"""Wiring for analyze_document — the state machine itself is covered exhaustively
in test_jobs.py; these prove the Celery task calls it correctly.

Runs against REAL Postgres with RLS enforced (pg_owner_engine to seed/read
rows directly, matching test_execute_analysis.py's pattern), NOT an
in-memory SQLite substitute. That substitution is exactly what let a real
bug ship silently: analyze_document used to run every DB call through a
plain SessionLocal() that never set app.user_id, so analysis_jobs' RLS
policy excluded every row (NULL compared to anything is NULL, not an
error) and no job was ever actually picked up. SQLite has no RLS concept
at all, so the old version of this file could never have caught that no
matter how thorough its assertions were (see DECISION_LOG.md 2026-07-20).
"""
from __future__ import annotations

import uuid

import pytest
from celery.exceptions import Retry
from sqlalchemy import text
from unittest.mock import patch

from app import worker
from app.jobs import MAX_RETRIES, TransientAnalysisError
from app.models import JobState


@pytest.fixture(autouse=True)
def eager_celery():
    worker.celery_app.conf.task_always_eager = True
    worker.celery_app.conf.task_eager_propagates = True
    yield


def _insert_user(pg_owner_engine, user_id: uuid.UUID) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
        conn.commit()


def _seed_job(pg_owner_engine) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    _insert_user(pg_owner_engine, user_id)
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, retry_count, created_at) "
                "VALUES (:id, :uid, :doc_id, 'abc', 'queued', 0, now())"
            ),
            {"id": uuid.uuid4(), "uid": user_id, "doc_id": doc_id},
        )
        conn.commit()
    return user_id, doc_id


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM analysis_jobs WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def _job_row(pg_owner_engine, user_id: uuid.UUID, doc_id: uuid.UUID):
    with pg_owner_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT state, retry_count, started_at, finished_at, error_reason "
                "FROM analysis_jobs WHERE user_id = :uid AND doc_id = :doc_id"
            ),
            {"uid": user_id, "doc_id": doc_id},
        ).fetchone()


def test_analyze_document_happy_path_and_unexpected_error(pg_owner_engine, cleanup_rows):
    user_id, doc_id = _seed_job(pg_owner_engine)
    cleanup_rows.append(user_id)
    with patch.object(worker, "_execute_analysis", return_value=None):
        worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()
    row = _job_row(pg_owner_engine, user_id, doc_id)
    assert row.state == JobState.SUCCEEDED.value
    assert row.started_at is not None and row.finished_at is not None

    user_id, doc_id = _seed_job(pg_owner_engine)
    cleanup_rows.append(user_id)
    with patch.object(worker, "_execute_analysis", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()
    row = _job_row(pg_owner_engine, user_id, doc_id)
    assert row.state == JobState.FAILED.value
    assert row.error_reason == "unexpected_error: boom"

    # no job row for this doc/user at all — should return quietly, not error
    worker.analyze_document.apply(args=(str(uuid.uuid4()), str(uuid.uuid4()))).get()


def test_analyze_document_dlq_dispatch_on_retry_exhaustion(pg_owner_engine, cleanup_rows):
    # Eager mode doesn't auto-recurse on self.retry() (it just raises Retry through
    # .get() instead of re-running the task) — a real worker would re-publish and
    # re-run the task after the backoff, so we drive that loop manually here:
    # MAX_RETRIES calls each raise Retry, then the (MAX_RETRIES + 1)th exhausts and
    # dead-letters instead.
    user_id, doc_id = _seed_job(pg_owner_engine)
    cleanup_rows.append(user_id)
    err = TransientAnalysisError("timeout", "upstream timed out")
    with patch.object(worker, "_execute_analysis", side_effect=err), patch.object(
        worker, "send_to_dead_letter"
    ) as mock_dlq:
        for _ in range(MAX_RETRIES):
            with pytest.raises(Retry):
                worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()
        worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()

    row = _job_row(pg_owner_engine, user_id, doc_id)
    assert row.state == JobState.FAILED.value
    assert row.retry_count == MAX_RETRIES
    mock_dlq.assert_called_once()


def test_analyze_document_actually_persists_through_rls_not_a_silent_noop(
    pg_owner_engine, cleanup_rows
):
    """Direct regression test for the RLS-scoping bug (DECISION_LOG.md
    2026-07-20): before the fix, this would pass `get_active_job` a session
    with app.user_id unset, RLS would silently exclude the row, and the job
    would still read back exactly as seeded -- started_at NULL, state
    unchanged -- with no exception anywhere to reveal it. Asserting the row
    actually changed is the whole point; asserting SUCCEEDED alone (as the
    happy-path test above does) wouldn't distinguish "it worked" from "it
    silently no-opped and _execute_analysis was never even called," which is
    exactly what makes this bug class dangerous.
    """
    user_id, doc_id = _seed_job(pg_owner_engine)
    cleanup_rows.append(user_id)
    before = _job_row(pg_owner_engine, user_id, doc_id)
    assert before.state == "queued" and before.started_at is None

    with patch.object(worker, "_execute_analysis", return_value=None) as mock_execute:
        worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()

    mock_execute.assert_called_once()
    after = _job_row(pg_owner_engine, user_id, doc_id)
    assert after.state == JobState.SUCCEEDED.value
    assert after.started_at is not None
    assert after.finished_at is not None

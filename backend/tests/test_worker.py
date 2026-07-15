"""Wiring for analyze_document — the state machine itself is covered exhaustively
in test_jobs.py; these just prove the Celery task calls it correctly.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.jobs import MAX_RETRIES, TransientAnalysisError, create_queued_job
from app.models import AnalysisJob, Base, JobState


@pytest.fixture(autouse=True)
def eager_celery(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", test_session_local)
    worker.celery_app.conf.task_always_eager = True
    worker.celery_app.conf.task_eager_propagates = True
    yield


def _seed_job() -> tuple[uuid.UUID, uuid.UUID]:
    with worker.SessionLocal() as session:
        job = create_queued_job(session, user_id=uuid.uuid4(), doc_id=uuid.uuid4(), doc_version_hash="abc")
        session.commit()
        return job.user_id, job.doc_id


def _final_state(user_id: uuid.UUID, doc_id: uuid.UUID) -> AnalysisJob:
    with worker.SessionLocal() as session:
        stmt = select(AnalysisJob).where(AnalysisJob.user_id == user_id, AnalysisJob.doc_id == doc_id)
        return session.execute(stmt).scalar_one()


def test_analyze_document_happy_path_and_unexpected_error():
    user_id, doc_id = _seed_job()
    with patch.object(worker, "_execute_analysis", return_value=None):
        worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()
    job = _final_state(user_id, doc_id)
    assert job.state == JobState.SUCCEEDED.value
    assert job.started_at is not None and job.finished_at is not None

    user_id, doc_id = _seed_job()
    with patch.object(worker, "_execute_analysis", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()
    job = _final_state(user_id, doc_id)
    assert job.state == JobState.FAILED.value
    assert job.error_reason == "unexpected_error: boom"

    # no job row for this doc/user at all — should return quietly, not error
    worker.analyze_document.apply(args=(str(uuid.uuid4()), str(uuid.uuid4()))).get()


def test_analyze_document_dlq_dispatch_on_retry_exhaustion():
    # Eager mode doesn't auto-recurse on self.retry() (it just raises Retry through
    # .get()) — a real worker would re-publish and re-run the task after the
    # backoff, so we drive that loop manually here: MAX_RETRIES calls each raise
    # Retry, then the (MAX_RETRIES + 1)th exhausts and dead-letters instead.
    user_id, doc_id = _seed_job()
    err = TransientAnalysisError("timeout", "upstream timed out")
    with patch.object(worker, "_execute_analysis", side_effect=err), patch.object(
        worker, "send_to_dead_letter"
    ) as mock_dlq:
        for _ in range(MAX_RETRIES):
            with pytest.raises(Retry):
                worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()
        worker.analyze_document.apply(args=(str(doc_id), str(user_id))).get()

    job = _final_state(user_id, doc_id)
    assert job.state == JobState.FAILED.value
    assert job.retry_count == MAX_RETRIES
    mock_dlq.assert_called_once()

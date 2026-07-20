"""analysis_jobs state machine per CONTRACTS.md §1. DB-only, no Celery import here,
so the worker can call these without a cycle and tests don't need a broker.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisJob, JobState, TERMINAL_STATES

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2


class TerminalStateError(Exception):
    """Raised when a caller tries to mutate a job already in succeeded/failed."""


class InvalidTransitionError(Exception):
    """Raised when a transition is attempted from a state CONTRACTS.md §1 doesn't allow it from."""


class TransientAnalysisError(Exception):
    """Raised by the analysis step for retryable failures (timeouts, connection errors)."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message
        super().__init__(f"{category}: {message}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_error(category: str, message: str) -> str:
    # Machine-readable category + short message only — never document content (CLAUDE.md rule 2).
    return f"{category}: {message[:200]}"


def _assert_not_terminal(job: AnalysisJob) -> None:
    if job.state in TERMINAL_STATES:
        raise TerminalStateError(f"job {job.id} is already terminal ({job.state}); no further writes allowed")


def _assert_from_state(job: AnalysisJob, expected: str) -> None:
    """Only the arrows CONTRACTS.md §1 draws are valid transitions: queued->running,
    running->succeeded, running->failed, running->queued. TerminalStateError takes
    priority over InvalidTransitionError so terminal-immutability checks stay precise.
    """
    _assert_not_terminal(job)
    if job.state != expected:
        raise InvalidTransitionError(f"job {job.id} is {job.state}, expected {expected}")


def _persist(session: Session, job: AnalysisJob) -> AnalysisJob:
    session.add(job)
    session.flush()
    return job


def backoff_seconds(retry_count: int) -> int:
    return BACKOFF_BASE_SECONDS**retry_count


def create_queued_job(session: Session, *, user_id: uuid.UUID, doc_id: uuid.UUID, doc_version_hash: str) -> AnalysisJob:
    job = AnalysisJob(user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash, state=JobState.QUEUED.value)
    return _persist(session, job)


def get_active_job(session: Session, *, user_id: uuid.UUID, doc_id: uuid.UUID) -> AnalysisJob | None:
    """Most recent non-terminal job for (user_id, doc_id) — a manual retry after a
    terminal failure creates a NEW row (terminal rows are immutable), so this always
    resolves to the one the worker should be operating on.
    """
    stmt = (
        select(AnalysisJob)
        .where(
            AnalysisJob.user_id == user_id,
            AnalysisJob.doc_id == doc_id,
            AnalysisJob.state.in_([JobState.QUEUED.value, JobState.RUNNING.value]),
        )
        .order_by(AnalysisJob.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def mark_running(session: Session, job: AnalysisJob) -> AnalysisJob:
    _assert_from_state(job, JobState.QUEUED.value)
    job.state = JobState.RUNNING.value
    if job.started_at is None:  # only the FIRST pickup sets started_at, per the contract
        job.started_at = _now()
    return _persist(session, job)


def mark_succeeded(
    session: Session, job: AnalysisJob, *, summary_error: str | None = None
) -> AnalysisJob:
    """summary_error is an already-formatted "category: message" string
    (worker._execute_summary's return value -- CONTRACTS.md §2c) for a
    summary-generation failure, supplementary to findings, so it never
    blocks success; None (the default) means summary generation succeeded
    or was never attempted, both unremarkable per the v1.4 worker-trigger
    design. Stored as-is, not re-formatted -- _execute_summary already
    applies the same _format_error (category + 200-char-truncated message)
    used for error_reason elsewhere in this module.
    """
    _assert_from_state(job, JobState.RUNNING.value)
    job.state = JobState.SUCCEEDED.value
    job.finished_at = _now()
    job.summary_error = summary_error
    return _persist(session, job)


def mark_failed(session: Session, job: AnalysisJob, *, category: str, message: str) -> AnalysisJob:
    """Immediate, non-retryable terminal failure."""
    _assert_from_state(job, JobState.RUNNING.value)
    job.state = JobState.FAILED.value
    job.error_reason = _format_error(category, message)
    job.finished_at = _now()
    return _persist(session, job)


def mark_transient_failure(session: Session, job: AnalysisJob, *, category: str, message: str) -> AnalysisJob:
    """Requeue with retry_count += 1 up to MAX_RETRIES; the next failure after that exhausts retries -> failed."""
    _assert_from_state(job, JobState.RUNNING.value)
    job.error_reason = _format_error(category, message)
    if job.retry_count < MAX_RETRIES:
        job.retry_count += 1
        job.state = JobState.QUEUED.value
    else:
        job.state = JobState.FAILED.value
        job.finished_at = _now()
    return _persist(session, job)

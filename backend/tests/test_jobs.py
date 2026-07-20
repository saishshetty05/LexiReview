from __future__ import annotations

import uuid

import pytest

from app.jobs import (
    MAX_RETRIES, TerminalStateError, backoff_seconds, create_queued_job, get_active_job,
    mark_failed, mark_running, mark_succeeded, mark_transient_failure,
)
from app.models import JobState


def _new_job(session):
    return create_queued_job(session, user_id=uuid.uuid4(), doc_id=uuid.uuid4(), doc_version_hash="deadbeef")


def test_happy_path_queued_running_succeeded(session):
    job = _new_job(session)
    assert job.state == JobState.QUEUED.value
    assert get_active_job(session, user_id=job.user_id, doc_id=job.doc_id).id == job.id

    mark_running(session, job)
    assert job.state == JobState.RUNNING.value
    assert job.started_at is not None
    first_started_at = job.started_at

    # a requeue-and-repickup (e.g. after a retry) must not move started_at
    job.state = JobState.QUEUED.value
    mark_running(session, job)
    assert job.started_at == first_started_at

    mark_succeeded(session, job)
    assert job.state == JobState.SUCCEEDED.value
    assert job.finished_at is not None
    assert job.summary_error is None
    assert get_active_job(session, user_id=job.user_id, doc_id=job.doc_id) is None


def test_mark_succeeded_records_summary_error_without_affecting_job_state(session):
    """CONTRACTS.md §2c: summary generation is supplementary -- a failure
    there is recorded on the job row, but the job itself still succeeds
    (state=succeeded, not failed), since findings are the primary output.
    """
    job = _new_job(session)
    mark_running(session, job)

    mark_succeeded(session, job, summary_error="provider_not_configured: no API key configured")

    assert job.state == JobState.SUCCEEDED.value
    assert job.summary_error == "provider_not_configured: no API key configured"


def test_mark_succeeded_summary_error_none_leaves_column_null(session):
    job = _new_job(session)
    mark_running(session, job)

    mark_succeeded(session, job, summary_error=None)

    assert job.summary_error is None


def test_each_transient_retry_requeues_then_exhaustion_fails(session):
    job = _new_job(session)
    mark_running(session, job)

    for expected_retry_count in range(1, MAX_RETRIES + 1):
        mark_transient_failure(session, job, category="timeout", message="upstream timed out")
        assert job.state == JobState.QUEUED.value
        assert job.retry_count == expected_retry_count
        assert job.finished_at is None
        assert job.error_reason == "timeout: upstream timed out"
        job.state = JobState.RUNNING.value  # worker picked it back up

    mark_transient_failure(session, job, category="timeout", message="upstream timed out")
    assert job.state == JobState.FAILED.value
    assert job.retry_count == MAX_RETRIES  # not incremented past the cap
    assert job.finished_at is not None


@pytest.mark.parametrize("terminal_maker", [mark_succeeded, lambda s, j: mark_failed(s, j, category="x", message="y")])
def test_terminal_states_are_never_overwritten(session, terminal_maker):
    job = _new_job(session)
    mark_running(session, job)
    terminal_maker(session, job)
    terminal_state = job.state
    finished_at = job.finished_at

    for mutator in (
        lambda: mark_running(session, job),
        lambda: mark_succeeded(session, job),
        lambda: mark_failed(session, job, category="x", message="y"),
        lambda: mark_transient_failure(session, job, category="x", message="y"),
    ):
        with pytest.raises(TerminalStateError):
            mutator()

    assert job.state == terminal_state
    assert job.finished_at == finished_at


def test_error_reason_format_and_backoff_math(session):
    job = _new_job(session)
    mark_running(session, job)
    mark_transient_failure(session, job, category="timeout", message="x" * 500)
    assert job.error_reason.startswith("timeout: ") and len(job.error_reason) < 250  # never full document content
    assert [backoff_seconds(n) for n in range(4)] == [1, 2, 4, 8]

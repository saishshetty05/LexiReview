"""Celery worker — Person B extends this per SETUP_GUIDE.md Phase 5."""
import logging
import os
import uuid

import anthropic
from celery import Celery
from sqlalchemy import select

from app.analysis_pipeline import run_analysis, run_summary
from app.beat_schedule import BEAT_SCHEDULE
from app.db import app_user_session
from app.decisions import get_severity_examples
from app.jobs import (
    MAX_RETRIES, TransientAnalysisError, _format_error, backoff_seconds,
    get_active_job, mark_failed, mark_running, mark_succeeded, mark_transient_failure,
)
from app.llm_client import LLMClient, LLMClientError, ProviderResponseError
from app.models import AnalysisCost, AnalysisJob, AnalysisResult, Document, DocumentSummary, JobState
from app.storage import fetch_document

# Anthropic errors that survive the SDK's own internal retry budget (see
# llm_client.ANTHROPIC_MAX_RETRIES) still deserve a job-level retry too --
# the PRD's reliability NFR asks for both a provider-level and a job-level
# retry layer, not one or the other. Guardrail refusals (PseudonymisationRequiredError,
# SyntheticOnlyViolationError, ProviderNotConfiguredError, SummaryGuardrailViolationError)
# are NOT included here: those are deterministic and terminal by design, not
# something a retry could fix. ProviderResponseError is the one LLMClientError
# subclass that IS included -- it means the provider replied but not in the
# expected tool-call shape, which (found live 2026-07-26, docs/DECISION_LOG.md)
# is a one-off model hiccup, not a property of the document: the identical
# document, re-queued immediately after an observed failure, succeeded cleanly
# on the very next call.
_TRANSIENT_LLM_ERRORS = (
    anthropic.APIConnectionError,  # covers APITimeoutError (subclass)
    anthropic.InternalServerError,
    anthropic.RateLimitError,
    anthropic.OverloadedError,
    ProviderResponseError,
)

logger = logging.getLogger(__name__)

celery_app = Celery("lexireview", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                    backend=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
celery_app.conf.broker_connection_retry_on_startup = True

# Outbox relay sweep (CONTRACTS.md §1/§5, v1.13): the `relay` service's
# `celery beat` process (app/beat.py, a separate minimal Celery app) ticks
# this same schedule and sends "relay.sweep_outbox" by name; this worker
# process is what actually consumes it and runs the task below. Registered
# here too (not just on beat_app) so `celery -A app.worker.celery_app worker`
# alone -- e.g. in a dev shell without the `relay` service running -- can
# still execute a sweep if something else triggers it.
celery_app.conf.beat_schedule = BEAT_SCHEDULE


@celery_app.task(name="ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="relay.sweep_outbox")
def relay_sweep_outbox() -> None:
    from app.relay import sweep_outbox

    sweep_outbox()


def _persist_cost_log(job: AnalysisJob, llm_client: LLMClient) -> None:
    """CONTRACTS.md §11 (v1.17): writes one AnalysisCost row per entry in
    llm_client.call_log. Must run on every path -- success, a transient
    failure that gets converted and re-raised, or an unexpected exception --
    since LLMCallUsage is appended immediately after messages.create()
    returns, before any parsing that could fail: by the time call_log has
    entries, the provider has already billed those tokens regardless of
    what happens to the job afterward. Skipped entirely when call_log is
    empty (no provider call was ever made -- a guardrail refusal, a fetch
    failure before any LLM call, etc), so this never writes a zero-usage
    row.

    A write failure here must not affect the job's own outcome -- same
    annotate-never-fail principle as _execute_summary's own error handling.
    """
    if not llm_client.call_log:
        return
    try:
        with app_user_session(job.user_id) as session:
            for entry in llm_client.call_log:
                session.add(
                    AnalysisCost(
                        job_id=job.id,
                        user_id=job.user_id,
                        call_type=entry.call_type,
                        model=entry.model,
                        input_tokens=entry.input_tokens,
                        output_tokens=entry.output_tokens,
                    )
                )
    except Exception:
        # Metadata only (CLAUDE.md rule 2: job_id, no document content) --
        # a cost-log write failure must not affect the job's own outcome,
        # but it shouldn't be silently invisible either.
        logger.warning("failed to persist cost log for job_id=%s", job.id, exc_info=True)


def _execute_analysis(job: AnalysisJob) -> list[dict]:
    """Fetch the document, run the analysis pipeline, and persist findings
    as AnalysisResult rows (CONTRACTS.md §2 Storage). Returns the same
    findings list, which analyze_document feeds to _execute_summary without
    a second query.

    document.is_synthetic (CONTRACTS.md §1a) is resolved from the same
    documents row already being fetched for file_type -- one query, not two
    seams -- and threaded straight through to LLMClient.analyze's guardrail.
    """
    with app_user_session(job.user_id) as session:
        document = session.execute(
            select(Document).where(
                Document.doc_id == job.doc_id,
                Document.doc_version_hash == job.doc_version_hash,
            )
        ).scalar_one_or_none()
        if document is None:
            # Immutable, RLS-scoped rows (CLAUDE.md rule 9) -- this only
            # happens if the job references a document that was never
            # ingested for this user, not a timing fluke. Not retryable.
            raise LookupError(
                f"no documents row for doc_id={job.doc_id} "
                f"doc_version_hash={job.doc_version_hash!r} under this job's user_id"
            )
        file_type = document.file_type
        is_synthetic = document.is_synthetic
        # CONTRACTS.md §7a (v1.8): fetched in the same session/query pass as
        # the document row, then threaded through to the (DB-free) pipeline.
        severity_examples = get_severity_examples(session, user_id=job.user_id)

    # fetch_document's typed errors (DocumentNotFoundError, VersionMismatchError,
    # ObjectMissingError) are all data-integrity problems, not transient ones --
    # the row we just read matched, so any of these means storage disagrees
    # with the database, which a retry cannot fix. Let them propagate to the
    # generic except Exception in analyze_document (terminal, unexpected_error).
    file_bytes = fetch_document(job.doc_id, job.user_id, job.doc_version_hash)

    llm_client = LLMClient()
    try:
        try:
            findings = run_analysis(
                file_bytes,
                file_type,
                llm_client,
                is_synthetic=is_synthetic,
                severity_examples=severity_examples,
            )
        except _TRANSIENT_LLM_ERRORS as exc:
            raise TransientAnalysisError("llm_provider_error", type(exc).__name__) from exc
    finally:
        # Runs on every path -- success, a transient error re-raised above,
        # or anything else propagating unexpected -- see _persist_cost_log's
        # docstring for why this can't be scoped to only the happy path.
        _persist_cost_log(job, llm_client)

    with app_user_session(job.user_id) as session:
        for finding in findings:
            session.add(
                AnalysisResult(
                    job_id=job.id,
                    user_id=job.user_id,
                    doc_id=job.doc_id,
                    doc_version_hash=job.doc_version_hash,
                    category=finding["category"],
                    severity=finding["severity"],
                    verification=finding["verification"],
                    confidence=finding["confidence"],
                    payload=finding,
                )
            )

    return findings


def _execute_summary(job: AnalysisJob, findings: list[dict]) -> str | None:
    """Generates and persists the document summary (CONTRACTS.md §2c) using
    the findings _execute_analysis already wrote for this job. Returns a
    "category: message" error string on any failure, and NEVER raises --
    summary generation is supplementary (findings are the primary output
    per the v1.4 decision), so nothing here may fail the job itself, and
    there is no job-level retry for it: a single best-effort attempt per
    job, same rationale as the summary_error column decision.

    Called from analyze_document after findings are secured, before
    mark_succeeded -- the returned error is threaded into mark_succeeded's
    summary_error kwarg, persisted to the nullable analysis_jobs.summary_error
    column in the same UPDATE that marks the job succeeded (CONTRACTS.md
    §2c's worker-trigger decision; migrations 004/005).
    """
    try:
        with app_user_session(job.user_id) as session:
            document = session.execute(
                select(Document).where(
                    Document.doc_id == job.doc_id,
                    Document.doc_version_hash == job.doc_version_hash,
                )
            ).scalar_one_or_none()
            if document is None:
                # Same "this can only mean a bad reference, not a timing
                # fluke" reasoning as _execute_analysis's identical check.
                raise LookupError(
                    f"no documents row for doc_id={job.doc_id} "
                    f"doc_version_hash={job.doc_version_hash!r} under this job's user_id"
                )
            file_type = document.file_type
            is_synthetic = document.is_synthetic

        file_bytes = fetch_document(job.doc_id, job.user_id, job.doc_version_hash)
        llm_client = LLMClient()
        try:
            payload = run_summary(
                file_bytes, file_type, llm_client, findings, is_synthetic=is_synthetic
            )
        finally:
            # Same "must run on every path" reasoning as _execute_analysis's
            # identical finally -- tokens are billed the moment the summarize
            # call returns, whether or not run_summary goes on to raise.
            _persist_cost_log(job, llm_client)

        with app_user_session(job.user_id) as session:
            session.add(
                DocumentSummary(
                    user_id=job.user_id,
                    doc_id=job.doc_id,
                    doc_version_hash=job.doc_version_hash,
                    model_version=llm_client.config.model,
                    payload=payload,
                )
            )
        return None
    except LLMClientError as exc:
        return _format_error(exc.category, exc.message)
    except Exception as exc:  # intentionally broad -- must never propagate, see docstring
        return _format_error("summary_generation_error", str(exc))


def send_to_dead_letter(job: AnalysisJob) -> None:
    dead_letter_record.apply_async(
        kwargs={
            "job_id": str(job.id),
            "doc_id": str(job.doc_id),
            "user_id": str(job.user_id),
            "error_reason": job.error_reason,
        },
        queue="dead_letter",
    )


@celery_app.task(name="dead_letter.record")
def dead_letter_record(job_id: str, doc_id: str, user_id: str, error_reason: str | None) -> None:
    # Sink for exhausted-retry jobs, IDs + metadata only (CLAUDE.md rule 2); a real consumer is future work.
    return None


@celery_app.task(bind=True, name="analyze_document")
def analyze_document(self, doc_id: str, user_id: str) -> None:
    """Pass-by-ID only, per CLAUDE.md rule 4 — no document content crosses the queue.

    Every DB touch here goes through its own app_user_session(uid) block,
    one per state transition, rather than a single session reused across
    several session.commit() calls. SET LOCAL is transaction-scoped: it does
    not survive a commit, so reusing one session/transaction across multiple
    commits (as this used to do with a plain SessionLocal(), which never set
    app.user_id at all) meant every query here ran with app.user_id unset.
    analysis_jobs' RLS policy (`user_id = current_setting('app.user_id',
    true)::uuid`) then silently returned ZERO rows for every query -- NULL
    compared to anything is NULL, not an error -- so get_active_job always
    found nothing and no job was ever actually picked up against real
    Postgres (see DECISION_LOG.md 2026-07-20). Unit tests never caught this:
    test_worker.py swapped in an in-memory SQLite engine with no RLS at all.
    """
    uid = uuid.UUID(user_id)

    with app_user_session(uid) as session:
        job = get_active_job(session, user_id=uid, doc_id=uuid.UUID(doc_id))
    if job is None:
        return

    with app_user_session(uid) as session:
        mark_running(session, job)

    try:
        findings = _execute_analysis(job)
    except TransientAnalysisError as exc:
        with app_user_session(uid) as session:
            job = mark_transient_failure(session, job, category=exc.category, message=exc.message)
        if job.state == JobState.FAILED.value:
            send_to_dead_letter(job)
            return
        raise self.retry(countdown=backoff_seconds(job.retry_count), max_retries=MAX_RETRIES)
    except Exception as exc:  # non-transient / unexpected — terminal, not retried
        with app_user_session(uid) as session:
            mark_failed(session, job, category="unexpected_error", message=str(exc))
        raise
    else:
        # Summary generation is supplementary (CONTRACTS.md §2c) and runs
        # after findings are secured, before the job is marked succeeded --
        # _execute_summary never raises, so a summary failure (e.g. no LLM
        # key configured yet) cannot fail the job itself, only annotate it.
        summary_error = _execute_summary(job, findings)
        with app_user_session(uid) as session:
            mark_succeeded(session, job, summary_error=summary_error)

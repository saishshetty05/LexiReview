"""Celery worker — Person B extends this per SETUP_GUIDE.md Phase 5."""
import os
import uuid

import anthropic
from celery import Celery
from sqlalchemy import select

from app.analysis_pipeline import run_analysis
from app.db import SessionLocal, app_user_session
from app.jobs import (
    MAX_RETRIES, TransientAnalysisError, backoff_seconds, get_active_job,
    mark_failed, mark_running, mark_succeeded, mark_transient_failure,
)
from app.llm_client import LLMClient
from app.models import AnalysisJob, AnalysisResult, Document, JobState
from app.storage import fetch_document

# Anthropic errors that survive the SDK's own internal retry budget (see
# llm_client.ANTHROPIC_MAX_RETRIES) still deserve a job-level retry too --
# the PRD's reliability NFR asks for both a provider-level and a job-level
# retry layer, not one or the other. Guardrail refusals (LLMClientError and
# its subclasses) are NOT included here: those are terminal by design, not
# something a retry could fix.
_TRANSIENT_ANTHROPIC_ERRORS = (
    anthropic.APIConnectionError,  # covers APITimeoutError (subclass)
    anthropic.InternalServerError,
    anthropic.RateLimitError,
    anthropic.OverloadedError,
)

celery_app = Celery("lexireview", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                    backend=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
celery_app.conf.broker_connection_retry_on_startup = True


@celery_app.task(name="ping")
def ping() -> str:
    return "pong"


def _execute_analysis(job: AnalysisJob) -> None:
    """Fetch the document, run the analysis pipeline, and persist findings
    as AnalysisResult rows (CONTRACTS.md §2 Storage).

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

    # fetch_document's typed errors (DocumentNotFoundError, VersionMismatchError,
    # ObjectMissingError) are all data-integrity problems, not transient ones --
    # the row we just read matched, so any of these means storage disagrees
    # with the database, which a retry cannot fix. Let them propagate to the
    # generic except Exception in analyze_document (terminal, unexpected_error).
    file_bytes = fetch_document(job.doc_id, job.user_id, job.doc_version_hash)

    llm_client = LLMClient()
    try:
        findings = run_analysis(file_bytes, file_type, llm_client, is_synthetic=is_synthetic)
    except _TRANSIENT_ANTHROPIC_ERRORS as exc:
        raise TransientAnalysisError("llm_provider_error", type(exc).__name__) from exc

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
    """Pass-by-ID only, per CLAUDE.md rule 4 — no document content crosses the queue."""
    with SessionLocal() as session:
        job = get_active_job(session, user_id=uuid.UUID(user_id), doc_id=uuid.UUID(doc_id))
        if job is None:
            return

        mark_running(session, job)
        session.commit()

        try:
            _execute_analysis(job)
        except TransientAnalysisError as exc:
            job = mark_transient_failure(session, job, category=exc.category, message=exc.message)
            session.commit()
            if job.state == JobState.FAILED.value:
                send_to_dead_letter(job)
                return
            raise self.retry(countdown=backoff_seconds(job.retry_count), max_retries=MAX_RETRIES)
        except Exception as exc:  # non-transient / unexpected — terminal, not retried
            mark_failed(session, job, category="unexpected_error", message=str(exc))
            session.commit()
            raise
        else:
            mark_succeeded(session, job)
            session.commit()

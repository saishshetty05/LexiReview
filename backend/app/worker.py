"""Celery worker — Person B extends this per SETUP_GUIDE.md Phase 5."""
import os
import uuid

from celery import Celery

from app.db import SessionLocal
from app.jobs import (
    MAX_RETRIES, TransientAnalysisError, backoff_seconds, get_active_job,
    mark_failed, mark_running, mark_succeeded, mark_transient_failure,
)
from app.models import AnalysisJob, JobState

celery_app = Celery("lexireview", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                    backend=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
celery_app.conf.broker_connection_retry_on_startup = True


@celery_app.task(name="ping")
def ping() -> str:
    return "pong"


def _execute_analysis(job: AnalysisJob) -> None:
    """Placeholder for extraction/anchors/LLM (Step 8, B2+) — a no-op is success for now."""
    return None


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

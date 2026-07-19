"""FastAPI app — grows per SETUP_GUIDE.md Phase 5.

main.py boundary (per A/B agreement): auth/upload routes are A's, job/findings
routes below are B's. Same file, don't pull each other's in-progress branches.
"""
import uuid

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select

from app.db import app_user_session
from app.deps import get_current_user_id
from app.models import AnalysisJob, AnalysisResult

app = FastAPI(title="LexiReview API")

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _parse_error_reason(error_reason: str) -> dict:
    # jobs._format_error always writes "category: message" -- partition on
    # the first ": " to invert it back into the contract's {category, message}.
    category, _, message = error_reason.partition(": ")
    return {"category": category, "message": message}


@app.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, user_id: uuid.UUID = Depends(get_current_user_id)) -> dict:
    """CONTRACTS.md §3(a) polling shape. No document text here -- only in
    /jobs/{id}/findings. A job_id that doesn't exist and one that exists but
    isn't owned by user_id both 404 identically: RLS (app_user_session) is
    what makes session.get return None either way, the same
    anti-enumeration-by-construction pattern as storage.fetch_document
    (CONTRACTS.md §4) -- not two branches that happen to return the same thing.
    """
    with app_user_session(user_id) as session:
        job = session.get(AnalysisJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            "job_id": str(job.id),
            "state": job.state,
            "retry_count": job.retry_count,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "error": _parse_error_reason(job.error_reason) if job.error_reason else None,
        }


@app.get("/jobs/{job_id}/findings")
def get_job_findings(
    job_id: uuid.UUID, user_id: uuid.UUID = Depends(get_current_user_id)
) -> list[dict]:
    """CONTRACTS.md §3(a): verified findings first, then unverified, each
    group sorted by severity (high -> info). Empty list for a job with no
    results yet (queued/running) -- not an error, just nothing to show.
    Same anti-enumeration 404 as get_job.
    """
    with app_user_session(user_id) as session:
        job = session.get(AnalysisJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        results = (
            session.execute(select(AnalysisResult).where(AnalysisResult.job_id == job_id))
            .scalars()
            .all()
        )

    verified = sorted(
        (r for r in results if r.verification == "verified"),
        key=lambda r: _SEVERITY_ORDER.get(r.severity, len(_SEVERITY_ORDER)),
    )
    unverified = sorted(
        (r for r in results if r.verification != "verified"),
        key=lambda r: _SEVERITY_ORDER.get(r.severity, len(_SEVERITY_ORDER)),
    )
    return [r.payload for r in (*verified, *unverified)]

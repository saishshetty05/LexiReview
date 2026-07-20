"""FastAPI app — grows per SETUP_GUIDE.md Phase 5.

main.py boundary (per A/B agreement): auth/upload routes are A's, job/findings
routes are B's. Same file, don't pull each other's in-progress branches.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from typing import Iterator

from celery import Celery
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    ACCESS_TOKEN_TTL_MINUTES,
    AuthError,
    InvalidTokenError,
    authenticate,
    create_access_token,
    decode_access_token,
    delete_account_cascade,
    register,
)
from app.db import app_user_session
from app.jobs import create_queued_job
from app.models import AnalysisJob, AnalysisResult, Document, DocumentSummary, User
from app.preflight import PreflightResult, run_preflight
from app.storage import put_document

app = FastAPI(title="LexiReview API")

ACCESS_TOKEN_COOKIE = "access_token"  # nosec B105 -- cookie name, not a credential

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

# category -> HTTP status for AuthError subclasses raised by app.auth.
_AUTH_ERROR_STATUS = {
    "invalid_email": 400,
    "invalid_password": 400,
    "email_already_registered": 409,
    "invalid_credentials": 401,
    "rate_limited": 429,
    "invalid_token": 401,
}


def _auth_error_response(exc: AuthError) -> HTTPException:
    status_code = _AUTH_ERROR_STATUS.get(exc.category, 400)
    return HTTPException(status_code=status_code, detail={"category": exc.category})


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _set_access_cookie(response: Response, token: str) -> None:
    # Secure cookies are dropped silently by browsers over plain HTTP
    # (Vite dev serves http://localhost:5173) -- login would 200 but the
    # cookie wouldn't stick, and everything after would 401. Fail-safe
    # default is secure=True (production-safe); only local compose dev
    # explicitly opts out via ENVIRONMENT=development (set in
    # docker-compose.yml, not requiring a manual .env edit). Found in PR
    # #39 review, flagged as out of scope there since app/auth.py is A's file.
    is_dev = os.environ.get("ENVIRONMENT") == "development"
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        token,
        httponly=True,
        secure=not is_dev,
        samesite="strict",
        max_age=ACCESS_TOKEN_TTL_MINUTES * 60,
    )


def get_current_user(request: Request) -> Iterator[tuple[User, Session]]:
    """Dependency for every protected endpoint: validates the JWT cookie and
    holds a request-scoped app_user_session open for its lifetime -- this is
    the enforcement point for constitution rule 1 (every query user_id-scoped)
    on the API side.
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail={"category": "missing_token"})
    try:
        user_id = decode_access_token(token)
    except InvalidTokenError as exc:
        raise _auth_error_response(exc) from exc
    with app_user_session(user_id) as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail={"category": "invalid_token"})
        yield user, session


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/register", status_code=201)
def auth_register(body: RegisterRequest) -> dict:
    try:
        user = register(body.email, body.password)
    except AuthError as exc:
        raise _auth_error_response(exc) from exc
    return {"user_id": str(user.id), "email": user.email}


@app.post("/auth/login")
def auth_login(body: LoginRequest, response: Response) -> dict:
    try:
        user = authenticate(body.email, body.password)
    except AuthError as exc:
        raise _auth_error_response(exc) from exc
    token = create_access_token(user.id)
    _set_access_cookie(response, token)
    return {"user_id": str(user.id)}


@app.post("/auth/logout")
def auth_logout(response: Response) -> dict:
    # Stateless JWT: logout is client-side token discard. Clearing the
    # cookie here covers the browser client; any other holder of the token
    # remains valid until it expires (ACCESS_TOKEN_TTL_MINUTES) -- no server
    # session table in this PR (see app/auth.py module docstring TODO).
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    return {"status": "logged_out"}


@app.delete("/auth/account")
def auth_delete_account(
    response: Response, current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    user, _session = current
    user_id: uuid.UUID = user.id
    delete_account_cascade(user_id)
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    return {"status": "deleted"}


def _parse_error_reason(error_reason: str) -> dict:
    # jobs._format_error always writes "category: message" -- partition on
    # the first ": " to invert it back into the contract's {category, message}.
    category, _, message = error_reason.partition(": ")
    return {"category": category, "message": message}


@app.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID, current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """CONTRACTS.md §3(a) polling shape. No document text here -- only in
    /jobs/{id}/findings. A job_id that doesn't exist and one that exists but
    isn't owned by the current user both 404 identically: RLS (the
    app_user_session opened by get_current_user) is what makes session.get
    return None either way, the same anti-enumeration-by-construction
    pattern as storage.fetch_document (CONTRACTS.md §4) -- not two branches
    that happen to return the same thing.
    """
    _user, session = current
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
    job_id: uuid.UUID, current: tuple[User, Session] = Depends(get_current_user)
) -> list[dict]:
    """CONTRACTS.md §3(a): verified findings first, then unverified, each
    group sorted by severity (high -> info). Empty list for a job with no
    results yet (queued/running) -- not an error, just nothing to show.
    Same anti-enumeration 404 as get_job.
    """
    _user, session = current
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


@app.get("/documents/{document_id}/summary")
def get_document_summary(
    document_id: uuid.UUID, current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """CONTRACTS.md §2b: "the document's current version and the pinned
    model_version." Two-step lookup, both RLS-scoped through the same
    session:

    1. Resolve the CURRENT version of document_id (highest `version`,
       documents.doc_id/version pair is immutable per row -- CLAUDE.md rule
       9 -- so "current" just means most recent version row). This step is
       also the anti-enumeration 404: a nonexistent document_id and one
       owned by someone else both produce zero visible rows via RLS -- one
       branch, one 404, same pattern as get_job/storage.fetch_document.
    2. Query document_summaries filtered to that version's doc_version_hash
       -- NOT any doc_version_hash ever seen for this doc_id. A prior
       version without a matching bugfix here would silently serve a STALE
       summary after a re-upload that hasn't been re-analyzed yet (caught
       in PR #33 review). If LLM_MODEL is set, also filter to that pinned
       model_version, per the contract wording; if unset (e.g. a dev
       environment before the Console key exists), fall back to the most
       recent summary at that hash by created_at -- documented here rather
       than silently picking an arbitrary row.
    """
    _user, session = current
    current_version = (
        session.execute(
            select(Document)
            .where(Document.doc_id == document_id)
            .order_by(Document.version.desc())
        )
        .scalars()
        .first()
    )
    if current_version is None:
        raise HTTPException(status_code=404, detail="document summary not found")

    summary_query = select(DocumentSummary).where(
        DocumentSummary.doc_version_hash == current_version.doc_version_hash
    )
    pinned_model_version = os.environ.get("LLM_MODEL")
    if pinned_model_version:
        summary_query = summary_query.where(
            DocumentSummary.model_version == pinned_model_version
        )
    summary_query = summary_query.order_by(DocumentSummary.created_at.desc())

    summary = session.execute(summary_query).scalars().first()
    if summary is None:
        raise HTTPException(status_code=404, detail="document summary not found")
    return summary.payload


# Producer-only Celery client: sends tasks by name without importing
# app.worker, which pulls in anthropic/llm_client/analysis_pipeline -- B's
# whole heavy dependency chain -- into A's upload endpoint. This is the
# standard Celery producer-side pattern; the task's *implementation* isn't
# needed to enqueue it, only the app's broker config.
_celery_producer = Celery(
    "lexireview-producer", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0")
)


def _enqueue_analysis(doc_id: uuid.UUID, user_id: uuid.UUID) -> None:
    # Task name verified against worker.py's @celery_app.task(bind=True,
    # name="analyze_document") decorator -- it explicitly overrides the
    # default dotted-path name, so "analyze_document" (not
    # "app.worker.analyze_document") is correct. Pass-by-ID only (CLAUDE.md
    # rule 4): no document content crosses the queue.
    _celery_producer.send_task("analyze_document", args=[str(doc_id), str(user_id)])


def _preflight_status_code(result: PreflightResult) -> int:
    # preflight.py returns a free-text reason + a metadata dict, not a
    # status-code enum -- map by which metadata keys are present, not by
    # brittle substring matching on the reason alone.
    reason = result.reason
    if "size_bytes" in result.metadata and "MB" in reason:
        return 413  # Payload Too Large
    if reason.startswith("Unsupported or unrecognized file type"):
        return 415  # Unsupported Media Type
    if "page_count" in result.metadata and "pages" in reason and "limit" in reason:
        return 413  # too many pages is also a size-class limit
    # Empty file, corrupted/encrypted PDF or DOCX, no extractable text,
    # context-limit exceeded: the file was readable but not processable.
    return 422  # Unprocessable Entity


@app.post("/documents/upload", status_code=201)
def upload_document(
    file: UploadFile = File(...), current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """FR-6 / CLAUDE.md rule 8: preflight passes BEFORE any storage or DB
    write happens -- nothing below the preflight call executes on rejection.

    Storage-failure handling: storage.put_document() is called INSIDE the
    same app_user_session block as the documents/analysis_jobs inserts, one
    commit at the end. If put_document raises, the exception propagates out
    of the `with` block and app_user_session's own except-clause rolls the
    transaction back -- no compensating DELETE is needed (and app_user has
    no DELETE grant on documents by design; only delete_account_cascade may
    ever use the owner connection). An orphaned documents row with no bytes
    in storage is worse than nothing: a later fetch_document/summary call
    would hit "found in DB, missing in storage" with no clean retry path.
    Rolling back lets the client just re-upload.

    FR-6 dedup: one documents.doc_version_hash match for the caller (RLS
    scopes the SELECT, no explicit user_id filter needed) returns 409 with
    the existing doc_id + its most recent job_id, so the client can poll
    /jobs/{job_id} instead of re-uploading. This is an app-level SELECT
    before INSERT, not a DB unique constraint -- two concurrent uploads of
    the identical file by the same user could both pass the check. Accepted
    gap for this PR; closing it needs a unique index on
    (user_id, doc_version_hash), a schema change out of scope here.
    """
    user, _outer_session = current
    contents = file.file.read()

    # Mirrors extraction.py's per-job random temp dir pattern (CLAUDE.md
    # rule 5): all file I/O for preflight happens inside it, purged in a
    # finally block regardless of accept/reject.
    job_dir = tempfile.mkdtemp(prefix="lexireview-upload-")
    try:
        upload_path = os.path.join(job_dir, "upload")
        with open(upload_path, "wb") as f:
            f.write(contents)
        result = run_preflight(upload_path)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)

    if not result.accepted:
        raise HTTPException(
            status_code=_preflight_status_code(result),
            detail={"category": "preflight_rejected", "message": result.reason},
        )

    sha256_hash = result.metadata["sha256"]
    file_type = result.metadata["file_type"]
    page_count = result.metadata.get("page_count")
    size_bytes = result.metadata["size_bytes"]
    # original_filename is sanitized-at-upload text only (models.py's
    # Document docstring) -- basename strips any path/traversal components.
    sanitized_filename = os.path.basename(file.filename or "upload")

    with app_user_session(user.id) as session:
        existing = (
            session.execute(select(Document).where(Document.doc_version_hash == sha256_hash))
            .scalars()
            .first()
        )
        if existing is not None:
            existing_job = (
                session.execute(
                    select(AnalysisJob)
                    .where(AnalysisJob.doc_id == existing.doc_id)
                    .order_by(AnalysisJob.created_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "category": "duplicate_document",
                    "message": "A document with this content has already been uploaded.",
                    "doc_id": str(existing.doc_id),
                    "job_id": str(existing_job.id) if existing_job else None,
                },
            )

        doc_id = uuid.uuid4()
        document = Document(
            doc_id=doc_id,
            user_id=user.id,
            version=1,
            doc_version_hash=sha256_hash,
            original_filename=sanitized_filename,
            file_type=file_type,
            page_count=page_count,
            size_bytes=size_bytes,
            is_synthetic=False,
        )
        session.add(document)
        job = create_queued_job(
            session, user_id=user.id, doc_id=doc_id, doc_version_hash=sha256_hash
        )
        job_id = job.id

        try:
            put_document(user.id, doc_id, 1, contents)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "category": "storage_failure",
                    "message": "Could not store the uploaded document. Please try again.",
                },
            ) from exc

    try:
        _enqueue_analysis(doc_id, user.id)
    except Exception as exc:
        # The documents/analysis_jobs rows are already committed (the
        # app_user_session block above already exited) -- unlike
        # put_document's failure, there is no transaction left to roll this
        # back into. The job is deliberately left `queued` rather than
        # marked failed: jobs.py's mark_failed only allows a running->failed
        # transition (CONTRACTS.md §1's state machine has no queued->failed
        # arrow), and adding one isn't a call to make under time pressure
        # without a contract sign-off. Known, accepted gap for this PR, same
        # as the dedup race above -- the row stays queued (a manual re-drive
        # would need a follow-up, tracked in CONTRACTS.md §5) rather than
        # silently losing the failure, and the caller gets an immediate,
        # structured signal instead of a generic 500.
        raise HTTPException(
            status_code=502,
            detail={
                "category": "broker_failure",
                "message": "Document was stored but analysis could not be queued. Please try again.",
                "doc_id": str(doc_id),
                "job_id": str(job_id),
            },
        ) from exc

    return {"doc_id": str(doc_id), "job_id": str(job_id), "state": "queued"}

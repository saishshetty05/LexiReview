"""Tests for worker._execute_analysis — the seam that fetches a document,
runs the analysis pipeline, and persists findings as AnalysisResult rows
(CONTRACTS.md §2 Storage). Runs against real Postgres (RLS matters here:
_execute_analysis reads/writes through app_user_session), with fetch_document
and run_analysis both faked so no real S3/MinIO or LLM provider is touched.
"""
from __future__ import annotations

import uuid

import anthropic
import httpx
import pytest
from sqlalchemy import text

from app import worker
from app.jobs import TransientAnalysisError
from app.models import AnalysisJob, JobState

_FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
_FAKE_RESPONSE = httpx.Response(500, request=_FAKE_REQUEST)


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


def _insert_document(
    pg_owner_engine,
    *,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    doc_version_hash: str,
    file_type: str = "docx",
    is_synthetic: bool = True,
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, doc_id, user_id, version, doc_version_hash, original_filename, "
                " file_type, size_bytes, is_synthetic, created_at) "
                "VALUES (:id, :doc_id, :user_id, 1, :hash, 'contract.docx', "
                " :file_type, 4, :is_synthetic, now())"
            ),
            {
                "id": uuid.uuid4(),
                "doc_id": doc_id,
                "user_id": user_id,
                "hash": doc_version_hash,
                "file_type": file_type,
                "is_synthetic": is_synthetic,
            },
        )
        conn.commit()


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        # analysis_results.job_id FKs to analysis_jobs -- delete children first.
        conn.execute(
            text("DELETE FROM analysis_results WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(
            text("DELETE FROM analysis_jobs WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(
            text("DELETE FROM documents WHERE user_id = ANY(:ids)"), {"ids": created_user_ids}
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def _make_job(
    pg_owner_engine, *, user_id: uuid.UUID, doc_id: uuid.UUID, doc_version_hash: str
) -> AnalysisJob:
    # analysis_results.job_id FKs to analysis_jobs, so the row must actually
    # exist -- _execute_analysis itself never queries analysis_jobs, but the
    # insert it performs on success does need a real parent row to satisfy
    # the constraint.
    job_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, retry_count, created_at, started_at) "
                "VALUES (:id, :user_id, :doc_id, :hash, :state, 0, now(), now())"
            ),
            {
                "id": job_id,
                "user_id": user_id,
                "doc_id": doc_id,
                "hash": doc_version_hash,
                "state": JobState.RUNNING.value,
            },
        )
        conn.commit()
    return AnalysisJob(
        id=job_id,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash=doc_version_hash,
        state=JobState.RUNNING.value,
    )


FINDING = {
    "category": "missing_clause",
    "severity": "medium",
    "block_ids": [],
    "evidence_quote": "",
    "explanation": "No arbitration clause was found in this document.",
    "verification": "verified",
    "confidence": "standard",
}


def test_execute_analysis_persists_findings_as_analysis_results(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-execute-analysis"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"synthetic contract bytes")
    monkeypatch.setattr(worker, "run_analysis", lambda *a, **k: [dict(FINDING)])

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_analysis(job)

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        rows = conn.execute(
            text(
                "SELECT job_id, doc_id, doc_version_hash, category, severity, "
                "verification, confidence, payload FROM analysis_results"
            )
        ).fetchall()
        conn.rollback()

    assert len(rows) == 1
    row = rows[0]
    assert str(row.job_id) == str(job.id)
    assert str(row.doc_id) == str(doc_id)
    assert row.doc_version_hash == doc_version_hash
    assert row.category == "missing_clause"
    assert row.severity == "medium"
    assert row.verification == "verified"
    assert row.confidence == "standard"
    assert row.payload["explanation"] == FINDING["explanation"]


def test_execute_analysis_passes_is_synthetic_from_the_document_row(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-is-synthetic"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash=doc_version_hash,
        is_synthetic=False,
    )

    received = {}

    def _fake_run_analysis(file_bytes, file_type, llm_client, *, is_synthetic):
        received["file_type"] = file_type
        received["is_synthetic"] = is_synthetic
        return []

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", _fake_run_analysis)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_analysis(job)

    assert received == {"file_type": "docx", "is_synthetic": False}


def test_execute_analysis_raises_lookup_error_when_document_row_missing(
    pg_owner_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    # No document row inserted for this doc_id/hash.

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"unreachable")
    monkeypatch.setattr(worker, "run_analysis", lambda *a, **k: [])

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash-missing")
    with pytest.raises(LookupError):
        worker._execute_analysis(job)


@pytest.mark.parametrize(
    "exc",
    [
        anthropic.APIConnectionError(request=_FAKE_REQUEST),
        anthropic.APITimeoutError(request=_FAKE_REQUEST),
        anthropic.RateLimitError("rate limited", response=_FAKE_RESPONSE, body=None),
        anthropic.InternalServerError("server error", response=_FAKE_RESPONSE, body=None),
        anthropic.OverloadedError("overloaded", response=_FAKE_RESPONSE, body=None),
    ],
)
def test_execute_analysis_wraps_transient_anthropic_errors(
    pg_owner_engine, cleanup_rows, monkeypatch, exc
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-transient"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    def _raise(*a, **k):
        raise exc

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", _raise)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    with pytest.raises(TransientAnalysisError) as exc_info:
        worker._execute_analysis(job)
    assert exc_info.value.category == "llm_provider_error"

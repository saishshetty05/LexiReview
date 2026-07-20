"""Tests for worker._execute_summary — the seam that generates and persists
a document summary (CONTRACTS.md §2c) using findings _execute_analysis
already produced. Runs against real Postgres (RLS matters here:
_execute_summary reads/writes through app_user_session), with
fetch_document and run_summary both faked so no real S3/MinIO or LLM
provider is touched.

NOT YET called from analyze_document -- see _execute_summary's docstring
for why (blocked on migration 004, analysis_jobs.summary_error). These
tests exercise the function directly, the same way test_execute_analysis.py
tests _execute_analysis directly ahead of/independent of the Celery task
wiring around it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app import worker
from app.llm_client import ProviderNotConfiguredError, SummaryGuardrailViolationError
from app.models import AnalysisJob, JobState


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
        conn.execute(
            text("DELETE FROM document_summaries WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(
            text("DELETE FROM documents WHERE user_id = ANY(:ids)"), {"ids": created_user_ids}
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def _make_job(*, user_id: uuid.UUID, doc_id: uuid.UUID, doc_version_hash: str) -> AnalysisJob:
    # Unlike _execute_analysis, _execute_summary never queries or writes
    # analysis_jobs itself (that's analyze_document's job, once wired up),
    # so this needs no real row -- a plain in-memory instance is enough.
    return AnalysisJob(
        id=uuid.uuid4(),
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash=doc_version_hash,
        state=JobState.RUNNING.value,
    )


def _stub_llm_client(monkeypatch, model: str = "claude-test-model") -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        worker, "LLMClient", lambda: SimpleNamespace(config=SimpleNamespace(model=model))
    )


FINDINGS = [
    {
        "category": "missing_clause",
        "severity": "medium",
        "block_ids": [],
        "evidence_quote": "",
        "explanation": "No arbitration clause was found in this document.",
        "verification": "verified",
        "confidence": "standard",
    }
]

SUMMARY_PAYLOAD = {
    "overview": "A short synthetic contract.",
    "key_terms": [{"label": "notice_period", "detail": "30 days"}],
    "risk_snapshot": {"high": 0, "medium": 1, "low": 0, "info": 0},
}


def test_execute_summary_persists_document_summary_row(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-execute-summary"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"synthetic contract bytes")
    monkeypatch.setattr(worker, "run_summary", lambda *a, **k: dict(SUMMARY_PAYLOAD))
    _stub_llm_client(monkeypatch, model="claude-test-model")

    job = _make_job(user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    result = worker._execute_summary(job, FINDINGS)

    assert result is None

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        rows = conn.execute(
            text(
                "SELECT doc_id, doc_version_hash, model_version, payload "
                "FROM document_summaries"
            )
        ).fetchall()
        conn.rollback()

    assert len(rows) == 1
    row = rows[0]
    assert str(row.doc_id) == str(doc_id)
    assert row.doc_version_hash == doc_version_hash
    assert row.model_version == "claude-test-model"
    assert row.payload == SUMMARY_PAYLOAD


def test_execute_summary_passes_findings_and_is_synthetic_to_run_summary(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-passthrough"
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

    def _fake_run_summary(file_bytes, file_type, llm_client, findings, *, is_synthetic):
        received["file_type"] = file_type
        received["findings"] = findings
        received["is_synthetic"] = is_synthetic
        return dict(SUMMARY_PAYLOAD)

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_summary", _fake_run_summary)
    _stub_llm_client(monkeypatch)

    job = _make_job(user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_summary(job, FINDINGS)

    assert received == {"file_type": "docx", "findings": FINDINGS, "is_synthetic": False}


def test_execute_summary_returns_error_string_when_document_row_missing(
    pg_owner_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    # No document row inserted for this doc_id/hash.

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"unreachable")
    monkeypatch.setattr(worker, "run_summary", lambda *a, **k: dict(SUMMARY_PAYLOAD))

    job = _make_job(user_id=user_id, doc_id=doc_id, doc_version_hash="hash-missing")
    result = worker._execute_summary(job, FINDINGS)

    assert result is not None
    assert result.startswith("summary_generation_error: ")


def test_execute_summary_returns_formatted_llm_client_error_without_raising(
    pg_owner_engine, cleanup_rows, monkeypatch
):
    """The defense-in-depth path: a guardrail refusal (e.g. no API key
    configured yet, or the rule-7 forbidden-language check) must surface as
    the exact category:message the caller will eventually store in
    analysis_jobs.summary_error -- not propagate and take the job down."""
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-guardrail"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    def _raise(*a, **k):
        raise ProviderNotConfiguredError("anthropic")

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_summary", _raise)
    _stub_llm_client(monkeypatch)

    job = _make_job(user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    result = worker._execute_summary(job, FINDINGS)

    assert result == "provider_not_configured: no API key configured for provider 'anthropic'"


def test_execute_summary_returns_formatted_error_on_rule_7_guardrail_violation(
    pg_owner_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-rule-7"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    def _raise(*a, **k):
        raise SummaryGuardrailViolationError()

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_summary", _raise)
    _stub_llm_client(monkeypatch)

    job = _make_job(user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    result = worker._execute_summary(job, FINDINGS)

    assert result == "summary_guardrail_violation: summary overview used forbidden safety-characterizing language"


def test_execute_summary_writes_no_row_when_it_fails(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-no-row-on-failure"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_summary", _raise)
    _stub_llm_client(monkeypatch)

    job = _make_job(user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_summary(job, FINDINGS)

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        rows = conn.execute(text("SELECT id FROM document_summaries")).fetchall()
        conn.rollback()
    assert rows == []

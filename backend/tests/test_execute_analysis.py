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
from app.llm_client import (
    ProviderNotConfiguredError,
    ProviderResponseError,
    PseudonymisationRequiredError,
    SyntheticOnlyViolationError,
)
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
        # decisions.finding_id FKs to analysis_results, which FKs to
        # analysis_jobs -- delete children first (same order as
        # test_decisions.py's cleanup_rows).
        conn.execute(
            text("DELETE FROM decisions WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
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


def _stub_llm_client(monkeypatch) -> None:
    """_execute_analysis constructs LLMClient() before calling run_analysis,
    which normally requires LLM_PROVIDER/LLM_MODEL to be set (LLMConfig.from_env).
    These tests fake run_analysis entirely, so the LLMClient instance itself
    is never used -- stub the constructor too rather than depend on real env
    config that CI has no reason to set just for these tests.
    """
    monkeypatch.setattr(worker, "LLMClient", lambda: object())


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
    _stub_llm_client(monkeypatch)

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

    def _fake_run_analysis(file_bytes, file_type, llm_client, *, is_synthetic, severity_examples=None):
        received["file_type"] = file_type
        received["is_synthetic"] = is_synthetic
        return []

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", _fake_run_analysis)
    _stub_llm_client(monkeypatch)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_analysis(job)

    assert received == {"file_type": "docx", "is_synthetic": False}


def test_execute_analysis_passes_severity_examples_from_past_overrides(
    pg_owner_engine, cleanup_rows, monkeypatch
):
    """CONTRACTS.md §7a (v1.8): a prior finding this user downgraded via
    severity_override must show up as a few-shot example on the NEXT
    analysis run for that same user, sourced from decisions+analysis_results,
    not passed by the caller."""
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-severity-examples"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    prior_job = _make_job(
        pg_owner_engine, user_id=user_id, doc_id=uuid.uuid4(), doc_version_hash="prior-hash"
    )
    prior_finding_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_results "
                "(id, job_id, user_id, doc_id, doc_version_hash, category, severity, "
                " verification, confidence, payload, created_at) "
                "VALUES (:id, :job_id, :user_id, :doc_id, 'other-hash', 'payment', 'high', "
                " 'verified', 'standard', :payload, now())"
            ),
            {
                "id": prior_finding_id,
                "job_id": prior_job.id,
                "user_id": user_id,
                "doc_id": uuid.uuid4(),
                "payload": '{"category": "payment", "severity": "high", "block_ids": [], '
                '"evidence_quote": "prior rent escalation clause", "explanation": "x"}',
            },
        )
        conn.execute(
            text(
                "INSERT INTO decisions (id, user_id, finding_id, decision, severity_override, "
                "created_at, updated_at) "
                "VALUES (:id, :user_id, :finding_id, 'accepted', 'medium', now(), now())"
            ),
            {"id": uuid.uuid4(), "user_id": user_id, "finding_id": prior_finding_id},
        )
        conn.commit()

    received = {}

    def _fake_run_analysis(file_bytes, file_type, llm_client, *, is_synthetic, severity_examples=None):
        received["severity_examples"] = severity_examples
        return []

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", _fake_run_analysis)
    _stub_llm_client(monkeypatch)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_analysis(job)

    assert received["severity_examples"] == [
        {
            "category": "payment",
            "evidence_quote": "prior rent escalation clause",
            "original_severity": "high",
            "severity_override": "medium",
        }
    ]


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
        ProviderResponseError("record_findings.findings was not a list"),
    ],
)
def test_execute_analysis_wraps_transient_llm_errors(
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
    _stub_llm_client(monkeypatch)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    with pytest.raises(TransientAnalysisError) as exc_info:
        worker._execute_analysis(job)
    assert exc_info.value.category == "llm_provider_error"


@pytest.mark.parametrize(
    "exc",
    [
        PseudonymisationRequiredError(),
        SyntheticOnlyViolationError("gemini_free"),
        ProviderNotConfiguredError("anthropic"),
    ],
)
def test_execute_analysis_does_not_wrap_guardrail_refusals(
    pg_owner_engine, cleanup_rows, monkeypatch, exc
):
    """Guardrail refusals are deterministic (same input -> same refusal every
    time), unlike ProviderResponseError's one-off malformed-shape hiccup --
    a retry can't fix them, so they must stay terminal, not get swept into
    _TRANSIENT_LLM_ERRORS by a future, too-broad `except LLMClientError`."""
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-guardrail-refusal"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    def _raise(*a, **k):
        raise exc

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", _raise)
    _stub_llm_client(monkeypatch)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    with pytest.raises(type(exc)):
        worker._execute_analysis(job)

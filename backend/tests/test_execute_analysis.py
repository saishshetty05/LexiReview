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
            text("DELETE FROM analysis_costs WHERE user_id = ANY(:ids)"),
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
    These tests fake run_analysis entirely, so the real provider machinery is
    never used -- stub the constructor too rather than depend on real env
    config that CI has no reason to set just for these tests. call_log is a
    real (mutable) list, not a stub value: _persist_cost_log (CONTRACTS.md
    §11) reads it after run_analysis returns/raises, and a fake run_analysis
    that wants to simulate a real provider call can append to it directly.
    """
    from types import SimpleNamespace

    monkeypatch.setattr(worker, "LLMClient", lambda: SimpleNamespace(call_log=[]))


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


# ── CONTRACTS.md §11 (v1.17): cost-log persistence ──────────────────────


def _cost_rows(pg_app_engine, user_id: uuid.UUID):
    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        rows = conn.execute(
            text(
                "SELECT job_id, call_type, model, input_tokens, output_tokens "
                "FROM analysis_costs"
            )
        ).fetchall()
        conn.rollback()
    return rows


def test_execute_analysis_persists_cost_log_on_success(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    from app.llm_client import LLMCallUsage

    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-cost-log-success"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    def _fake_run_analysis(file_bytes, file_type, llm_client, *, is_synthetic, severity_examples=None):
        llm_client.call_log.append(
            LLMCallUsage(call_type="analyze", model="claude-test", input_tokens=100, output_tokens=20)
        )
        llm_client.call_log.append(
            LLMCallUsage(call_type="entailment", model="claude-test", input_tokens=10, output_tokens=5)
        )
        return [dict(FINDING)]

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", _fake_run_analysis)
    _stub_llm_client(monkeypatch)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_analysis(job)

    rows = _cost_rows(pg_app_engine, user_id)
    assert len(rows) == 2
    assert {r.call_type for r in rows} == {"analyze", "entailment"}
    assert all(str(r.job_id) == str(job.id) for r in rows)


def test_execute_analysis_persists_cost_log_even_on_transient_failure(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    """The count-semantics rule in CONTRACTS.md §11 depends on this: real
    provider spend must be captured even when the call that spent it ends
    up raising (ProviderResponseError -- the response came back and tokens
    were billed, but the shape didn't parse). Without this, a job that
    burns real tokens and then exhausts retries would leak quota: real
    spend, zero analysis_costs rows, silently uncounted.
    """
    from app.llm_client import LLMCallUsage

    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-cost-log-transient"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    def _fake_run_analysis(file_bytes, file_type, llm_client, *, is_synthetic, severity_examples=None):
        llm_client.call_log.append(
            LLMCallUsage(call_type="analyze", model="claude-test", input_tokens=100, output_tokens=20)
        )
        raise ProviderResponseError("record_findings.findings was not a list")

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", _fake_run_analysis)
    _stub_llm_client(monkeypatch)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    with pytest.raises(TransientAnalysisError):
        worker._execute_analysis(job)

    rows = _cost_rows(pg_app_engine, user_id)
    assert len(rows) == 1
    assert rows[0].call_type == "analyze"
    assert rows[0].input_tokens == 100


def test_execute_analysis_writes_no_cost_rows_when_call_log_empty(
    pg_owner_engine, pg_app_engine, cleanup_rows, monkeypatch
):
    """A guardrail refusal, or any failure before a real provider call, is
    never appended to call_log -- must write zero rows, never a
    zero-usage placeholder row."""
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-cost-log-empty"
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash
    )

    monkeypatch.setattr(worker, "fetch_document", lambda *a, **k: b"bytes")
    monkeypatch.setattr(worker, "run_analysis", lambda *a, **k: [])
    _stub_llm_client(monkeypatch)

    job = _make_job(pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash=doc_version_hash)
    worker._execute_analysis(job)

    assert _cost_rows(pg_app_engine, user_id) == []

"""Tests for GET /documents (list user's recent documents).

Runs against real Postgres (RLS matters -- documents must be scoped to the user).
"""
from __future__ import annotations

import io
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


def _insert_document(
    pg_owner_engine,
    *,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    version: int,
    doc_version_hash: str,
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, doc_id, user_id, version, doc_version_hash, original_filename, "
                "file_type, size_bytes, is_synthetic, created_at) "
                "VALUES (:id, :doc_id, :uid, :version, :hash, 'f.pdf', 'pdf', 10, true, now())"
            ),
            {
                "id": uuid.uuid4(),
                "doc_id": doc_id,
                "uid": user_id,
                "version": version,
                "hash": doc_version_hash,
            },
        )
        conn.commit()


def _insert_job(
    pg_owner_engine,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    doc_version_hash: str,
    state: str,
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, created_at) "
                "VALUES (:id, :uid, :doc_id, :hash, :state, now())"
            ),
            {
                "id": job_id,
                "uid": user_id,
                "doc_id": doc_id,
                "hash": doc_version_hash,
                "state": state,
            },
        )
        conn.commit()


def _set_job_state(pg_owner_engine, job_id: str, state: str) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("UPDATE analysis_jobs SET state = :state WHERE id = :job_id"),
            {"state": state, "job_id": job_id},
        )
        conn.commit()


def _insert_finding(
    pg_owner_engine,
    *,
    job_id: str,
    user_id: uuid.UUID,
    doc_id: str,
    doc_version_hash: str,
    severity: str,
    verification: str,
) -> None:
    payload = {
        "category": "payment",
        "severity": severity,
        "block_ids": ["BLOCK_1"],
        "evidence_quote": "synthetic quote",
        "explanation": "synthetic explanation",
        "verification": verification,
        "confidence": "standard",
    }
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_results "
                "(id, job_id, user_id, doc_id, doc_version_hash, category, severity, "
                "verification, confidence, payload, created_at) "
                "VALUES (:id, :job_id, :uid, :doc_id, :hash, 'payment', :severity, "
                ":verification, 'standard', :payload, now())"
            ),
            {
                "id": uuid.uuid4(),
                "job_id": job_id,
                "uid": user_id,
                "doc_id": doc_id,
                "hash": doc_version_hash,
                "severity": severity,
                "verification": verification,
                "payload": json.dumps(payload),
            },
        )
        conn.commit()


@pytest.fixture()
def s3_env(monkeypatch):
    if os.environ.get("S3_ENDPOINT"):
        yield
        return

    from moto import mock_aws

    monkeypatch.setenv("S3_ENDPOINT", "https://s3.amazonaws.com")
    monkeypatch.setenv("MINIO_ROOT_USER", "testing")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testing")
    monkeypatch.setenv("S3_BUCKET", "documents-test")
    with mock_aws():
        yield


def _register_user(pg_owner_engine) -> tuple[uuid.UUID, dict[str, str]]:
    email = f"list-docs-test-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    token = create_access_token(user.id)
    return user.id, {ACCESS_TOKEN_COOKIE: token}


def _valid_pdf_bytes(content: str = "LEASE AGREEMENT") -> bytes:
    """Generate a PDF with unique content to avoid SHA-256 dedup."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 10, content)
    return bytes(pdf.output())


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
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


def test_list_documents_empty_for_new_user(pg_owner_engine, cleanup_rows, s3_env):
    """A user with no uploads gets an empty list."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    resp = client.get("/documents", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_documents_returns_users_documents_ordered_by_created_at_desc(
    pg_owner_engine, cleanup_rows, s3_env
):
    """Returns up to 5 documents, most recent first, with latest job info."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    # Upload 3 documents with unique content
    uploaded_doc_ids = []
    uploaded_job_ids = []
    for i in range(3):
        pdf_bytes = _valid_pdf_bytes(f"LEASE AGREEMENT {i} - {uuid.uuid4()}")
        resp = client.post(
            "/documents/upload",
            cookies=auth_cookies,
            files={"file": (f"doc{i}.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert resp.status_code == 201
        uploaded_doc_ids.append(resp.json()["doc_id"])
        uploaded_job_ids.append(resp.json()["job_id"])

    resp = client.get("/documents", cookies=auth_cookies)
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 3

    # Most recent first
    assert docs[0]["doc_id"] == uploaded_doc_ids[2]
    assert docs[1]["doc_id"] == uploaded_doc_ids[1]
    assert docs[2]["doc_id"] == uploaded_doc_ids[0]

    # Each has job info
    for i, doc in enumerate(docs):
        assert doc["original_filename"] == f"doc{2 - i}.pdf"
        assert doc["file_type"] == "pdf"
        assert doc["latest_job"] is not None
        assert doc["latest_job"]["state"] == "queued"


def test_list_documents_limits_to_five(pg_owner_engine, cleanup_rows, s3_env):
    """Only the 5 most recent documents are returned."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    # Upload 7 documents with unique content
    for i in range(7):
        pdf_bytes = _valid_pdf_bytes(f"LEASE AGREEMENT {i} - {uuid.uuid4()}")
        resp = client.post(
            "/documents/upload",
            cookies=auth_cookies,
            files={"file": (f"doc{i}.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert resp.status_code == 201

    resp = client.get("/documents", cookies=auth_cookies)
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 5

    # Should be the 5 most recent (doc6, doc5, doc4, doc3, doc2)
    assert docs[0]["original_filename"] == "doc6.pdf"
    assert docs[4]["original_filename"] == "doc2.pdf"


def test_list_documents_rls_user_cannot_see_other_users_docs(
    pg_owner_engine, cleanup_rows, s3_env
):
    """RLS ensures a user only sees their own documents."""
    user_a_id, auth_a = _register_user(pg_owner_engine)
    user_b_id, auth_b = _register_user(pg_owner_engine)
    cleanup_rows.extend([user_a_id, user_b_id])

    # User A uploads a document
    pdf_bytes = _valid_pdf_bytes()
    resp_a = client.post(
        "/documents/upload",
        cookies=auth_a,
        files={"file": ("user_a_doc.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp_a.status_code == 201
    doc_a_id = resp_a.json()["doc_id"]

    # User B uploads a document
    resp_b = client.post(
        "/documents/upload",
        cookies=auth_b,
        files={"file": ("user_b_doc.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp_b.status_code == 201
    doc_b_id = resp_b.json()["doc_id"]

    # User A's list only shows their document
    resp = client.get("/documents", cookies=auth_a)
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == doc_a_id

    # User B's list only shows their document
    resp = client.get("/documents", cookies=auth_b)
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == doc_b_id


def test_list_documents_requires_auth():
    """Unauthenticated requests are rejected."""
    resp = client.get("/documents")
    assert resp.status_code == 401


def test_list_documents_includes_job_state(
    pg_owner_engine, cleanup_rows, s3_env
):
    """The latest_job includes state, created_at, and finished_at."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    pdf_bytes = _valid_pdf_bytes()
    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 201

    resp = client.get("/documents", cookies=auth_cookies)
    docs = resp.json()
    assert len(docs) == 1

    job = docs[0]["latest_job"]
    assert job is not None
    assert job["state"] == "queued"
    assert "job_id" in job
    assert "created_at" in job
    assert job["finished_at"] is None  # Job hasn't completed yet


def _empty_counts() -> dict:
    return {
        "high": {"verified": 0, "unverified": 0},
        "medium": {"verified": 0, "unverified": 0},
        "low": {"verified": 0, "unverified": 0},
        "info": {"verified": 0, "unverified": 0},
    }


def test_finding_counts_null_when_no_job(pg_owner_engine, cleanup_rows, s3_env):
    """No analysis ever run -> finding_counts is null, not zero."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash1"
    )

    resp = client.get("/documents", cookies=auth_cookies)
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["latest_job"] is None
    assert docs[0]["finding_counts"] is None


@pytest.mark.parametrize("state", ["queued", "running", "failed"])
def test_finding_counts_null_when_job_not_succeeded(
    pg_owner_engine, cleanup_rows, s3_env, state
):
    """Analysis started but not (yet) succeeded -> finding_counts stays
    null -- must stay distinguishable from "succeeded, zero findings"."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    pdf_bytes = _valid_pdf_bytes()
    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    _set_job_state(pg_owner_engine, job_id, state)

    resp = client.get("/documents", cookies=auth_cookies)
    docs = resp.json()
    assert docs[0]["latest_job"]["state"] == state
    assert docs[0]["finding_counts"] is None


def test_finding_counts_all_zero_when_succeeded_with_no_findings(
    pg_owner_engine, cleanup_rows, s3_env
):
    """Succeeded with genuinely zero findings -> all-zero counts, not null.
    The frontend is responsible for rendering this as the required rule-7
    phrase, not badges -- this test only asserts the API's own contract."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    pdf_bytes = _valid_pdf_bytes()
    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    _set_job_state(pg_owner_engine, job_id, "succeeded")

    resp = client.get("/documents", cookies=auth_cookies)
    docs = resp.json()
    assert docs[0]["finding_counts"] == _empty_counts()


def test_finding_counts_grouped_by_severity_and_verification(
    pg_owner_engine, cleanup_rows, s3_env
):
    """Counts are grouped exactly as the plan specifies: per severity, a
    verified/unverified split -- unverified findings are counted, never
    dropped (constitution rule 6)."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    pdf_bytes = _valid_pdf_bytes()
    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 201
    doc_id = resp.json()["doc_id"]
    job_id = resp.json()["job_id"]
    _set_job_state(pg_owner_engine, job_id, "succeeded")

    for severity, verification in [
        ("high", "verified"),
        ("high", "verified"),
        ("high", "unverified"),
        ("medium", "unverified"),
        ("info", "verified"),
    ]:
        _insert_finding(
            pg_owner_engine,
            job_id=job_id,
            user_id=user_id,
            doc_id=doc_id,
            doc_version_hash="irrelevant-hash",
            severity=severity,
            verification=verification,
        )

    resp = client.get("/documents", cookies=auth_cookies)
    docs = resp.json()
    expected = _empty_counts()
    expected["high"] = {"verified": 2, "unverified": 1}
    expected["medium"] = {"verified": 0, "unverified": 1}
    expected["info"] = {"verified": 1, "unverified": 0}
    assert docs[0]["finding_counts"] == expected


def test_finding_counts_scoped_to_latest_job_not_doc_id(
    pg_owner_engine, cleanup_rows, s3_env
):
    """Regression for the plan's core claim: documents are versioned/
    immutable (constitution rule 9), so finding_counts must be scoped to the
    SPECIFIC job_id shown on that row, never summed across every job that
    shares a doc_id.

    /documents/upload always mints a fresh doc_id today -- there is no live
    re-upload-to-existing-doc_id path yet (rule 9's versioning exists at the
    schema/docstring level only, per models.py/storage.py), and GET
    /documents has no dedup-by-doc_id (separate, pre-existing gap, out of
    scope here). So both document versions and both jobs are inserted
    directly, same pattern as test_document_summary_api.py's equivalent
    re-upload regression test, and both rows are expected back.
    """
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()

    # v1: analyzed, one high/verified finding.
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash-v1"
    )
    job_v1 = uuid.uuid4()
    _insert_job(
        pg_owner_engine, job_id=job_v1, user_id=user_id, doc_id=doc_id,
        doc_version_hash="hash-v1", state="succeeded",
    )
    _insert_finding(
        pg_owner_engine, job_id=job_v1, user_id=user_id, doc_id=doc_id,
        doc_version_hash="hash-v1", severity="high", verification="verified",
    )

    # v2: same doc_id, new version row (re-upload) -- analyzed with a
    # different finding.
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=2, doc_version_hash="hash-v2"
    )
    job_v2 = uuid.uuid4()
    _insert_job(
        pg_owner_engine, job_id=job_v2, user_id=user_id, doc_id=doc_id,
        doc_version_hash="hash-v2", state="succeeded",
    )
    _insert_finding(
        pg_owner_engine, job_id=job_v2, user_id=user_id, doc_id=doc_id,
        doc_version_hash="hash-v2", severity="low", verification="verified",
    )

    resp = client.get("/documents", cookies=auth_cookies)
    docs = resp.json()
    # NOTE: GET /documents has no dedup-by-doc_id today (pre-existing, out of
    # scope here -- see PR description), so both version rows come back as
    # separate cards. What this test actually pins down: each row's
    # finding_counts reflects ONLY its own doc_version_hash's job, never a
    # sum across every job that shares the doc_id.
    assert len(docs) == 2

    v2_expected = _empty_counts()
    v2_expected["low"] = {"verified": 1, "unverified": 0}
    v1_expected = _empty_counts()
    v1_expected["high"] = {"verified": 1, "unverified": 0}

    counts_by_job_id = {doc["latest_job"]["job_id"]: doc["finding_counts"] for doc in docs}
    assert counts_by_job_id[str(job_v1)] == v1_expected
    assert counts_by_job_id[str(job_v2)] == v2_expected  # v1's high finding must NOT be summed in

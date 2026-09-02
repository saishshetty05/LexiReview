"""Tests for GET /documents (list user's recent documents).

Runs against real Postgres (RLS matters -- documents must be scoped to the user).
"""
from __future__ import annotations

import io
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


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

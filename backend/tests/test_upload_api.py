"""Tests for POST /documents/upload (FR-6, CLAUDE.md rule 8).

Runs against real Postgres (RLS matters -- documents/analysis_jobs rows must
be scoped to the uploading user) plus either real compose MinIO
(S3_ENDPOINT set) or moto (S3_ENDPOINT unset, e.g. CI) -- same s3_env
pattern as test_storage.py. The Celery producer call (app.main._enqueue_analysis)
is monkeypatched to a recorder rather than hitting a real broker: these are
API/DB/storage tests, not a Celery integration test, and asserting the
recorded (task name, args) is what actually catches a silently-wrong task
name (the exact risk flagged in PR review), not just "it didn't raise."
"""
from __future__ import annotations

import io
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

import app.main as main_module
from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app
from app.preflight import MAX_UPLOAD_BYTES

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


@pytest.fixture()
def recorded_tasks(monkeypatch):
    """Replaces the real Celery producer's send_task with a recorder, so
    tests assert the exact (task name, args) sent -- catching a silently
    wrong task name, which would otherwise leave jobs queued forever with
    no error surface.
    """
    calls: list[tuple[str, list]] = []

    def fake_send_task(name, args=None, **kwargs):
        calls.append((name, args))

    monkeypatch.setattr(main_module._celery_producer, "send_task", fake_send_task)
    return calls


def _register_user(pg_owner_engine) -> tuple[uuid.UUID, dict[str, str]]:
    email = f"upload-test-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    token = create_access_token(user.id)
    return user.id, {ACCESS_TOKEN_COOKIE: token}


def _valid_pdf_bytes() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(
        0, 10, "LEASE AGREEMENT between PERSON_1 and PERSON_2. "
               "Monthly rent: AMOUNT_1. Termination requires 60 days notice."
    )
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


def test_upload_happy_path_creates_document_and_queued_job(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks
):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    pdf_bytes = _valid_pdf_bytes()

    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "queued"
    doc_id = uuid.UUID(body["doc_id"])
    job_id = uuid.UUID(body["job_id"])

    with pg_owner_engine.connect() as conn:
        doc_row = conn.execute(
            text("SELECT user_id, version, file_type, original_filename FROM documents WHERE doc_id = :id"),
            {"id": doc_id},
        ).fetchone()
        job_row = conn.execute(
            text("SELECT user_id, doc_id, state FROM analysis_jobs WHERE id = :id"), {"id": job_id}
        ).fetchone()

    assert doc_row is not None
    assert doc_row.user_id == user_id
    assert doc_row.version == 1
    assert doc_row.file_type == "pdf"
    assert doc_row.original_filename == "lease.pdf"

    assert job_row is not None
    assert job_row.user_id == user_id
    assert job_row.doc_id == doc_id
    assert job_row.state == "queued"

    # The Celery task name is verified explicitly, not assumed -- worker.py
    # registers it as "analyze_document" (short name override), not the
    # dotted default "app.worker.analyze_document".
    assert recorded_tasks == [("analyze_document", [str(doc_id), str(user_id)])]


def test_upload_preflight_rejection_writes_nothing(pg_owner_engine, cleanup_rows, s3_env, recorded_tasks):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    oversized = b"\0" * (MAX_UPLOAD_BYTES + 1)

    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
    )

    assert resp.status_code == 413
    assert resp.json()["detail"]["category"] == "preflight_rejected"

    with pg_owner_engine.connect() as conn:
        doc_count = conn.execute(
            text("SELECT count(*) FROM documents WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
        job_count = conn.execute(
            text("SELECT count(*) FROM analysis_jobs WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
    assert doc_count == 0
    assert job_count == 0
    assert recorded_tasks == []


def test_upload_duplicate_sha256_returns_409_with_existing_doc_id(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks
):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    pdf_bytes = _valid_pdf_bytes()

    first = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert first.status_code == 201
    existing_doc_id = first.json()["doc_id"]
    existing_job_id = first.json()["job_id"]

    second = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease-again.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["category"] == "duplicate_document"
    assert detail["doc_id"] == existing_doc_id
    assert detail["job_id"] == existing_job_id

    with pg_owner_engine.connect() as conn:
        doc_count = conn.execute(
            text("SELECT count(*) FROM documents WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
    assert doc_count == 1  # the duplicate upload did not create a second row

    # Only the first upload enqueued a task.
    assert recorded_tasks == [("analyze_document", [existing_doc_id, str(user_id)])]


def test_upload_requires_auth(s3_env, recorded_tasks):
    resp = client.post(
        "/documents/upload",
        files={"file": ("lease.pdf", io.BytesIO(_valid_pdf_bytes()), "application/pdf")},
    )
    assert resp.status_code == 401
    assert recorded_tasks == []


def test_upload_storage_failure_rolls_back_document_row(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks, monkeypatch
):
    """The core rollback-strategy behavior: if storage.put_document raises
    inside the app_user_session block, the whole transaction -- including
    the documents row already added in the same session -- must roll back.
    An orphaned documents row with no bytes in storage would be worse than
    nothing (a later fetch would hit object_missing with no clean retry).
    """
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    def failing_put_document(*args, **kwargs):
        raise RuntimeError("simulated MinIO outage")

    monkeypatch.setattr(main_module, "put_document", failing_put_document)

    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(_valid_pdf_bytes()), "application/pdf")},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["category"] == "storage_failure"

    with pg_owner_engine.connect() as conn:
        doc_count = conn.execute(
            text("SELECT count(*) FROM documents WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
        job_count = conn.execute(
            text("SELECT count(*) FROM analysis_jobs WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
    assert doc_count == 0
    assert job_count == 0
    assert recorded_tasks == []

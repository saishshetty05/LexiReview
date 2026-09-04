"""Tests for POST /documents/upload (FR-6, CLAUDE.md rule 8).

Runs against real Postgres (RLS matters -- documents/analysis_jobs rows must
be scoped to the uploading user) plus either real compose MinIO
(S3_ENDPOINT set) or moto (S3_ENDPOINT unset, e.g. CI) -- same s3_env
pattern as test_storage.py. Upload no longer makes a broker call at all
(CONTRACTS.md §1/§5, v1.13): it writes an `outbox` row atomically with
documents/analysis_jobs, and app/relay.py delivers it later. These are
API/DB/storage tests, not a Celery integration test, so `recorded_tasks`
reads the outbox row(s) back and asserts their (task_name, args) payload
directly -- catching a silently-wrong task name/args (the exact risk flagged
in PR review), not just "it didn't raise."
"""
from __future__ import annotations

import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

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
def recorded_tasks(pg_owner_engine):
    """Reads back the outbox row(s) `upload_document` wrote, in creation
    order, as (task_name, args) tuples -- same shape the old Celery-producer
    monkeypatch recorded, so existing assertions didn't need to change shape,
    only how they're populated.
    """
    def _read() -> list[tuple[str, list]]:
        with pg_owner_engine.connect() as conn:
            rows = conn.execute(text("SELECT payload_json FROM outbox ORDER BY created_at")).fetchall()
        return [(row.payload_json["task_name"], row.payload_json["args"]) for row in rows]

    return _read


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
    assert recorded_tasks() == [("analyze_document", [str(doc_id), str(user_id)])]


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
    assert recorded_tasks() == []


# ── CONTRACTS.md §11 (v1.17): monthly quota enforcement ───────────────────


def _seed_used_quota(pg_owner_engine, *, user_id: uuid.UUID, count: int) -> None:
    """Seeds `count` distinct (job, analysis_costs row) pairs this UTC
    month for `user_id` -- simulates jobs that already made a real LLM
    provider call, the thing check_quota's COUNT(DISTINCT job_id) counts.
    """
    with pg_owner_engine.connect() as conn:
        for _ in range(count):
            job_id, doc_id = uuid.uuid4(), uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO analysis_jobs "
                    "(id, user_id, doc_id, doc_version_hash, state, retry_count, created_at) "
                    "VALUES (:id, :uid, :doc_id, 'seed-hash', 'succeeded', 0, now())"
                ),
                {"id": job_id, "uid": user_id, "doc_id": doc_id},
            )
            conn.execute(
                text(
                    "INSERT INTO analysis_costs "
                    "(id, job_id, user_id, call_type, model, input_tokens, output_tokens, created_at) "
                    "VALUES (:id, :job_id, :uid, 'analyze', 'claude-test', 10, 5, now())"
                ),
                {"id": uuid.uuid4(), "job_id": job_id, "uid": user_id},
            )
        conn.commit()


def test_upload_returns_429_when_quota_exceeded_and_writes_nothing(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks, monkeypatch
):
    monkeypatch.setenv("ANALYSIS_QUOTA_MONTHLY", "2")
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _seed_used_quota(pg_owner_engine, user_id=user_id, count=2)

    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(_valid_pdf_bytes()), "application/pdf")},
    )

    assert resp.status_code == 429, resp.text
    detail = resp.json()["detail"]
    assert detail["category"] == "quota_exceeded"
    assert detail["limit"] == 2
    assert detail["used"] == 2
    assert "resets_at" in detail

    # Same "rejection writes nothing" property as preflight rejection --
    # only the 2 seeded jobs exist, nothing new from this rejected upload.
    with pg_owner_engine.connect() as conn:
        doc_count = conn.execute(
            text("SELECT count(*) FROM documents WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
        job_count = conn.execute(
            text("SELECT count(*) FROM analysis_jobs WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
    assert doc_count == 0
    assert job_count == 2  # the seeded rows only
    assert recorded_tasks() == []


def test_upload_succeeds_one_below_quota_limit(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks, monkeypatch
):
    monkeypatch.setenv("ANALYSIS_QUOTA_MONTHLY", "2")
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _seed_used_quota(pg_owner_engine, user_id=user_id, count=1)

    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(_valid_pdf_bytes()), "application/pdf")},
    )

    assert resp.status_code == 201, resp.text


def test_upload_quota_default_is_200_when_env_unset(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks, monkeypatch
):
    monkeypatch.delenv("ANALYSIS_QUOTA_MONTHLY", raising=False)
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    # Nowhere near 200 -- just confirms the default doesn't reject a normal upload.

    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(_valid_pdf_bytes()), "application/pdf")},
    )

    assert resp.status_code == 201, resp.text


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
    assert recorded_tasks() == [("analyze_document", [existing_doc_id, str(user_id)])]


def test_upload_concurrent_duplicate_uploads_create_exactly_one_document(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks
):
    """FR-6 dedup race (docs/DECISION_LOG.md, 2026-07-20 / migration 007):
    two concurrent uploads of the identical file by the same user must
    still produce exactly one `documents` row -- one request wins with 201,
    the other loses with the same 409 duplicate_document body as the
    non-concurrent case.

    This reproduces reliably without an artificial synchronization barrier:
    the winning request's row lock is held from its `session.flush()` in
    upload_document() until the *end* of its `app_user_session` block --
    which is after create_queued_job() and the put_document() storage
    write, not right after the flush. That gives a real, not contrived,
    window during which the losing thread's own flush() blocks on
    Postgres's unique-index lock, then raises IntegrityError once the
    winner commits. Both threads' initial duplicate-check SELECT almost
    always sees zero rows (neither has committed yet), so both attempt the
    insert -- guaranteeing exactly one winner.
    """
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    pdf_bytes = _valid_pdf_bytes()

    def do_upload():
        return client.post(
            "/documents/upload",
            cookies=auth_cookies,
            files={"file": ("lease.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result() for future in [pool.submit(do_upload) for _ in range(2)]]

    assert sorted(r.status_code for r in responses) == [201, 409]

    winner = next(r for r in responses if r.status_code == 201)
    loser = next(r for r in responses if r.status_code == 409)
    loser_detail = loser.json()["detail"]
    assert loser_detail["category"] == "duplicate_document"
    assert loser_detail["doc_id"] == winner.json()["doc_id"]

    with pg_owner_engine.connect() as conn:
        doc_count = conn.execute(
            text("SELECT count(*) FROM documents WHERE user_id = :id"), {"id": user_id}
        ).scalar_one()
    assert doc_count == 1


def test_upload_requires_auth(s3_env, recorded_tasks):
    resp = client.post(
        "/documents/upload",
        files={"file": ("lease.pdf", io.BytesIO(_valid_pdf_bytes()), "application/pdf")},
    )
    assert resp.status_code == 401
    assert recorded_tasks() == []


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
    assert recorded_tasks() == []


def test_upload_makes_no_broker_call_and_leaves_outbox_for_relay(
    pg_owner_engine, cleanup_rows, s3_env, recorded_tasks
):
    """CONTRACTS.md §1/§5 (v1.13): upload makes no broker call at all, so a
    broker outage at upload time can no longer surface as a request failure
    -- delivery is entirely deferred to app/relay.py's sweep, which reads
    this same outbox row. This replaces the old
    test_upload_broker_failure_returns_502_and_leaves_job_queued, whose 502
    broker_failure response CONTRACTS.md v1.13 explicitly retires (there is
    no broker call left in the request to fail).
    """
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    resp = client.post(
        "/documents/upload",
        cookies=auth_cookies,
        files={"file": ("lease.pdf", io.BytesIO(_valid_pdf_bytes()), "application/pdf")},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    doc_id, job_id = body["doc_id"], body["job_id"]

    with pg_owner_engine.connect() as conn:
        job_row = conn.execute(
            text("SELECT state FROM analysis_jobs WHERE id = :id"), {"id": job_id}
        ).fetchone()
        outbox_row = conn.execute(
            text("SELECT delivered_at, attempts FROM outbox WHERE job_id = :id"), {"id": job_id}
        ).fetchone()
    assert job_row is not None
    assert job_row.state == "queued"
    # The row is durably queued for delivery regardless of broker reachability
    # -- delivery status lives on the outbox row, never the job's own state
    # column (CONTRACTS.md §1's tightened `queued` semantics, v1.13).
    assert outbox_row is not None
    assert outbox_row.delivered_at is None
    assert outbox_row.attempts == 0
    assert recorded_tasks() == [("analyze_document", [doc_id, str(user_id)])]

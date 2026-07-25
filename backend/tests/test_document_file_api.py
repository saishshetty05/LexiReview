"""Tests for GET /documents/{id}/file -- backs the commercial-UI redesign's
real DocumentViewer (frontend has never had one; the panel was a literal
placeholder before this endpoint existed).

Same pattern as test_document_summary_api.py: real Postgres (RLS matters),
real get_current_user auth, real compose MinIO (S3_ENDPOINT set) or moto
otherwise (same s3_env fixture as test_upload_api.py) since this endpoint's
whole job is serving real bytes out of storage.fetch_document -- mocking
storage would test nothing.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app
from app.storage import put_document

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
    email = f"doc-file-test-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    token = create_access_token(user.id)
    return user.id, {ACCESS_TOKEN_COOKIE: token}


def _insert_document(
    pg_owner_engine,
    *,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    version: int,
    doc_version_hash: str,
    file_type: str = "pdf",
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, doc_id, user_id, version, doc_version_hash, original_filename, "
                "file_type, size_bytes, is_synthetic, created_at) "
                "VALUES (:id, :doc_id, :uid, :version, :hash, 'f.' || :file_type, "
                ":file_type, 10, true, now())"
            ),
            {
                "id": uuid.uuid4(),
                "doc_id": doc_id,
                "uid": user_id,
                "version": version,
                "hash": doc_version_hash,
                "file_type": file_type,
            },
        )
        conn.commit()


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM documents WHERE user_id = ANY(:ids)"), {"ids": created_user_ids}
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def test_returns_real_bytes_and_content_type_for_pdf(pg_owner_engine, cleanup_rows, s3_env):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    file_bytes = b"%PDF-1.4 fake pdf bytes for test\n"
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash1", file_type="pdf"
    )
    put_document(user_id, doc_id, 1, file_bytes)

    resp = client.get(f"/documents/{doc_id}/file", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.content == file_bytes
    assert resp.headers["content-type"] == "application/pdf"


def test_returns_docx_content_type(pg_owner_engine, cleanup_rows, s3_env):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    file_bytes = b"PK\x03\x04 fake docx bytes for test"
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash1", file_type="docx"
    )
    put_document(user_id, doc_id, 1, file_bytes)

    resp = client.get(f"/documents/{doc_id}/file", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.content == file_bytes
    assert (
        resp.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_serves_current_version_not_a_stale_one(pg_owner_engine, cleanup_rows, s3_env):
    """Same "resolve current version first" discipline as the summary
    endpoint: a re-upload (new version, new hash) must serve the NEW
    bytes, not the old version's, even though both rows exist."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    old_bytes = b"old version bytes"
    new_bytes = b"new version bytes"
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash-v1"
    )
    put_document(user_id, doc_id, 1, old_bytes)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=2, doc_version_hash="hash-v2"
    )
    put_document(user_id, doc_id, 2, new_bytes)

    resp = client.get(f"/documents/{doc_id}/file", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.content == new_bytes


def test_404_for_nonexistent_document(pg_owner_engine, cleanup_rows, s3_env):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    resp = client.get(f"/documents/{uuid.uuid4()}/file", cookies=auth_cookies)
    assert resp.status_code == 404


def test_404_for_not_owned_document_identical_to_nonexistent(pg_owner_engine, cleanup_rows, s3_env):
    """Anti-enumeration: same discipline as every other document-scoped
    endpoint (get_document_summary, storage.fetch_document) -- a real
    doc_id owned by someone else must be indistinguishable from one that
    doesn't exist at all."""
    owner_id, _owner_cookies = _register_user(pg_owner_engine)
    other_id, other_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(owner_id)
    cleanup_rows.append(other_id)
    doc_id = uuid.uuid4()
    _insert_document(pg_owner_engine, user_id=owner_id, doc_id=doc_id, version=1, doc_version_hash="hash1")
    put_document(owner_id, doc_id, 1, b"owner's bytes")

    not_owned_resp = client.get(f"/documents/{doc_id}/file", cookies=other_cookies)
    nonexistent_resp = client.get(f"/documents/{uuid.uuid4()}/file", cookies=other_cookies)

    assert not_owned_resp.status_code == nonexistent_resp.status_code == 404
    assert not_owned_resp.json() == nonexistent_resp.json()


def test_404_for_missing_token(pg_owner_engine, cleanup_rows, s3_env):
    resp = client.get(f"/documents/{uuid.uuid4()}/file")
    assert resp.status_code == 401

"""Tests for app/storage.py (CONTRACTS.md §4).

Run against the real, migrated schema (see conftest.py's pg_owner_engine /
pg_app_engine docstrings) plus either real compose MinIO (S3_ENDPOINT set)
or moto (S3_ENDPOINT unset, e.g. CI).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from app.storage import DocumentNotFoundError, ObjectMissingError, fetch_document, put_document


@pytest.fixture()
def s3_env(monkeypatch):
    """Env-gated: real MinIO if S3_ENDPOINT is already configured (local dev,
    per docker-compose/.env), else moto (CI, or any environment without a
    real object store available).
    """
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
    version: int,
    doc_version_hash: str,
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, doc_id, user_id, version, doc_version_hash, original_filename, "
                " file_type, size_bytes, created_at) "
                "VALUES (:id, :doc_id, :user_id, :version, :hash, 'contract.pdf', "
                " 'pdf', 4, now())"
            ),
            {
                "id": uuid.uuid4(),
                "doc_id": doc_id,
                "user_id": user_id,
                "version": version,
                "hash": doc_version_hash,
            },
        )
        conn.commit()


@pytest.fixture()
def cleanup_users(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM documents WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def test_round_trip_put_then_fetch(s3_env, pg_owner_engine, cleanup_users):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-round-trip"
    cleanup_users.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash=doc_version_hash
    )

    put_document(user_id, doc_id, 1, b"synthetic contract bytes")

    result = fetch_document(doc_id, user_id, doc_version_hash)
    assert result == b"synthetic contract bytes"


def test_not_owned_document_raises_document_not_found(s3_env, pg_owner_engine, cleanup_users):
    owner_id, other_user_id, doc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-not-owned"
    cleanup_users.append(owner_id)
    cleanup_users.append(other_user_id)
    _insert_user(pg_owner_engine, owner_id)
    _insert_user(pg_owner_engine, other_user_id)
    _insert_document(
        pg_owner_engine, user_id=owner_id, doc_id=doc_id, version=1, doc_version_hash=doc_version_hash
    )
    put_document(owner_id, doc_id, 1, b"synthetic contract bytes")

    with pytest.raises(DocumentNotFoundError):
        fetch_document(doc_id, other_user_id, doc_version_hash)


def test_missing_object_raises_object_missing(s3_env, pg_owner_engine, cleanup_users):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    doc_version_hash = "hash-missing-object"
    cleanup_users.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash=doc_version_hash
    )
    # Ensures the bucket exists (via an unrelated key) without ever writing
    # the v1 blob the row above points at -- the row exists, that blob does not.
    put_document(user_id, doc_id, 999, b"unrelated placeholder object")

    with pytest.raises(ObjectMissingError):
        fetch_document(doc_id, user_id, doc_version_hash)

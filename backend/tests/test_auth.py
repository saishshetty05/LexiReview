"""Tests for app/auth.py + the /auth/* endpoints in app/main.py.

Runs against the real, migrated Postgres schema (see conftest.py's
pg_owner_engine docstring) — auth.py's app_user_session-backed functions
connect via the same APP_DATABASE_URL/DATABASE_URL env the api container
uses, so no separate test-only wiring is needed.
"""
from __future__ import annotations

import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import (
    InvalidTokenError,
    _login_attempts,
    decode_access_token,
    delete_account_cascade,
)
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


def _unique_email() -> str:
    return f"auth-test-{uuid.uuid4()}@example.invalid"


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    _login_attempts.clear()
    yield
    _login_attempts.clear()


@pytest.fixture()
def registered_user(pg_owner_engine):
    email = _unique_email()
    password = "TestPass123!"
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    user_id = uuid.UUID(resp.json()["user_id"])
    yield user_id, email, password
    with pg_owner_engine.connect() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        conn.commit()


def test_register_success_and_duplicate_email_rejected(registered_user):
    _user_id, email, _password = registered_user
    resp = client.post("/auth/register", json={"email": email, "password": "AnotherPass1!"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["category"] == "email_already_registered"


def test_login_success_sets_cookie(registered_user):
    _user_id, email, password = registered_user
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    assert ACCESS_TOKEN_COOKIE in resp.cookies


def test_login_wrong_password_rejected(registered_user):
    _user_id, email, _password = registered_user
    resp = client.post("/auth/login", json={"email": email, "password": "wrong-password-1!"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "invalid_credentials"


def test_login_rate_limited_after_too_many_attempts(registered_user):
    _user_id, email, password = registered_user
    for _ in range(5):
        resp = client.post("/auth/login", json={"email": email, "password": "wrong"})
        assert resp.status_code == 401
    # 6th attempt, even with the correct password, is rate-limited.
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 429
    assert resp.json()["detail"]["category"] == "rate_limited"


def test_get_current_user_rejects_missing_token():
    resp = client.delete("/auth/account")
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "missing_token"


def test_get_current_user_rejects_tampered_token():
    resp = client.delete(
        "/auth/account", cookies={ACCESS_TOKEN_COOKIE: "not-a-real-token"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "invalid_token"


def test_decode_access_token_rejects_expired_token(monkeypatch):
    import os

    monkeypatch.setenv("JWT_SECRET", os.environ.get("JWT_SECRET", "test-secret"))
    expired = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": time.time() - 1000, "exp": time.time() - 1},
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(expired)


def test_delete_account_removes_all_rows_and_nulls_audit_log(pg_owner_engine, registered_user):
    user_id, email, password = registered_user

    doc_id = uuid.uuid4()
    job_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, doc_id, user_id, version, doc_version_hash, original_filename, "
                "file_type, size_bytes, is_synthetic, created_at) "
                "VALUES (:id, :doc_id, :uid, 1, 'hash1', 'f.pdf', 'pdf', 10, true, now())"
            ),
            {"id": uuid.uuid4(), "doc_id": doc_id, "uid": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, created_at) "
                "VALUES (:id, :uid, :doc_id, 'hash1', 'queued', now())"
            ),
            {"id": job_id, "uid": user_id, "doc_id": doc_id},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_results "
                "(id, job_id, user_id, doc_id, doc_version_hash, category, severity, "
                "verification, confidence, payload, created_at) "
                "VALUES (:id, :job_id, :uid, :doc_id, 'hash1', 'liability', 'high', "
                "'verified', 'standard', '{}'::jsonb, now())"
            ),
            {"id": uuid.uuid4(), "job_id": job_id, "uid": user_id, "doc_id": doc_id},
        )
        conn.execute(
            text(
                "INSERT INTO document_summaries "
                "(id, user_id, doc_id, doc_version_hash, model_version, payload, created_at) "
                "VALUES (:id, :uid, :doc_id, 'hash1', 'model-v1', '{}'::jsonb, now())"
            ),
            {"id": uuid.uuid4(), "uid": user_id, "doc_id": doc_id},
        )
        audit_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO audit_log (id, event_type, user_id, doc_id, created_at) "
                "VALUES (:id, 'login', :uid, :doc_id, now())"
            ),
            {"id": audit_id, "uid": user_id, "doc_id": doc_id},
        )
        conn.commit()

    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    token = login_resp.cookies[ACCESS_TOKEN_COOKIE]

    resp = client.delete("/auth/account", cookies={ACCESS_TOKEN_COOKIE: token})
    assert resp.status_code == 200, resp.text

    with pg_owner_engine.connect() as conn:
        for table in ("documents", "analysis_jobs", "analysis_results", "document_summaries"):
            count = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :uid"), {"uid": user_id}
            ).scalar_one()
            assert count == 0, f"{table} still has rows for deleted user"

        remaining_users = conn.execute(
            text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": user_id}
        ).scalar_one()
        assert remaining_users == 0

        audit_user_id = conn.execute(
            text("SELECT user_id FROM audit_log WHERE id = :id"), {"id": audit_id}
        ).scalar_one()
        assert audit_user_id is None
        conn.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": audit_id})
        conn.commit()


def test_delete_account_cascade_function_is_idempotent_on_missing_user(pg_owner_engine):
    # Calling the cascade for a user_id that doesn't exist must not raise --
    # every DELETE/UPDATE is a no-op WHERE match, not an error.
    result = delete_account_cascade(uuid.uuid4())
    assert result.documents_deleted == 0

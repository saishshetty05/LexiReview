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
from app.main import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE, app

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
        # refresh_tokens FKs into users.id with no ON DELETE CASCADE (same
        # as every other user-owned table) -- a login in the test body
        # creates a row here, so it must go before the users delete below.
        conn.execute(text("DELETE FROM refresh_tokens WHERE user_id = :id"), {"id": user_id})
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


def test_login_cookie_is_secure_by_default(registered_user, monkeypatch):
    # Fail-safe default: unset (or any value other than exactly
    # "development") keeps Secure on. resp.cookies (httpx's CookieJar)
    # doesn't preserve attributes like Secure -- assert on the raw
    # Set-Cookie header instead.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    _user_id, email, password = registered_user
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    assert "Secure" in resp.headers["set-cookie"]


def test_login_cookie_is_not_secure_in_development(registered_user, monkeypatch):
    # PR #39 review finding: Vite dev serves plain http://localhost:5173,
    # and browsers silently drop Secure cookies there -- login would 200
    # but the cookie wouldn't stick, and everything after would 401.
    monkeypatch.setenv("ENVIRONMENT", "development")
    _user_id, email, password = registered_user
    try:
        resp = client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200
        assert "Secure" not in resp.headers["set-cookie"]
    finally:
        # Every other cookie in this file is Secure, and httpx's TestClient
        # jar (scheme http://testserver) silently refuses to persist a
        # Secure cookie across requests -- this test is the ONLY one that
        # produces a non-Secure cookie, so it's the only one that can leak
        # a stale cookie into later tests (e.g.
        # test_get_current_user_rejects_missing_token expecting none)
        # unless explicitly cleared here.
        client.cookies.clear()


def test_login_wrong_password_rejected(registered_user):
    _user_id, email, _password = registered_user
    resp = client.post("/auth/login", json={"email": email, "password": "wrong-password-1!"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "invalid_credentials"


# ── Refresh tokens (CONTRACTS.md §9, v1.11) ────────────────────────────────


def test_login_sets_refresh_cookie(registered_user):
    _user_id, email, password = registered_user
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    assert REFRESH_TOKEN_COOKIE in resp.cookies


def test_refresh_rotates_both_cookies(registered_user):
    _user_id, email, password = registered_user
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    refresh_token = login_resp.cookies[REFRESH_TOKEN_COOKIE]

    resp = client.post("/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: refresh_token})
    assert resp.status_code == 200, resp.text
    assert ACCESS_TOKEN_COOKIE in resp.cookies
    assert resp.cookies[REFRESH_TOKEN_COOKIE] != refresh_token


def test_refresh_missing_cookie_rejected():
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "invalid_token"


def test_refresh_unknown_token_rejected():
    resp = client.post("/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: "not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "invalid_token"


def test_refresh_reuse_of_rotated_token_revokes_the_whole_chain(registered_user):
    """CONTRACTS.md §9 reuse detection: replaying an already-rotated token
    is treated as suspected theft -- it revokes every outstanding token for
    the user, not just the one that was replayed. Verified here by proving
    the legitimately-rotated token from the first call dies too.
    """
    _user_id, email, password = registered_user
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    original_token = login_resp.cookies[REFRESH_TOKEN_COOKIE]

    first_refresh = client.post("/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: original_token})
    assert first_refresh.status_code == 200
    rotated_token = first_refresh.cookies[REFRESH_TOKEN_COOKIE]

    reuse_resp = client.post("/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: original_token})
    assert reuse_resp.status_code == 401
    assert reuse_resp.json()["detail"]["category"] == "invalid_token"

    rotated_now_dead = client.post("/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: rotated_token})
    assert rotated_now_dead.status_code == 401
    assert rotated_now_dead.json()["detail"]["category"] == "invalid_token"


def test_refresh_revoke_endpoint_revokes_token(registered_user):
    """CONTRACTS.md §9: POST /auth/refresh/revoke is the server-side
    revocation path the frontend calls before /auth/logout. In a real browser
    the cookie's path=/auth/refresh scoping delivers it to
    /auth/refresh/revoke via RFC 6265 prefix matching (/auth/refresh is a
    prefix of /auth/refresh/revoke). Here we pass it explicitly because the
    TestClient's cookie jar doesn't reliably deliver Secure cookies over
    http://testserver across httpx versions.
    """
    _user_id, email, password = registered_user
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    refresh_token = login_resp.cookies[REFRESH_TOKEN_COOKIE]

    revoke_resp = client.post(
        "/auth/refresh/revoke",
        cookies={REFRESH_TOKEN_COOKIE: refresh_token},
    )
    assert revoke_resp.status_code == 200

    resp = client.post("/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: refresh_token})
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "invalid_token"


def test_logout_is_client_side_clear_and_does_not_revoke(registered_user):
    """POST /auth/logout is back to a pure client-side cookie clear (its
    original stateless-JWT role). It cannot revoke server-side — the
    refresh_token cookie's path=/auth/refresh is never attached to
    /auth/logout by RFC 6265 prefix matching, and revocation is the revoke
    endpoint's job. Guards the §9 split.
    """
    _user_id, email, password = registered_user
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    refresh_token = login_resp.cookies[REFRESH_TOKEN_COOKIE]

    logout_resp = client.post("/auth/logout")
    assert logout_resp.status_code == 200

    # Logout alone leaves the token valid -- client-side clear only; the
    # frontend's logout() calls /auth/refresh/revoke before this.
    resp = client.post("/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: refresh_token})
    assert resp.status_code == 200


def test_logout_without_refresh_cookie_still_succeeds():
    # No refresh_token cookie sent at all -- logout must not require one
    # (e.g. a session that predates this feature, or one already expired).
    resp = client.post("/auth/logout")
    assert resp.status_code == 200


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
        for table in (
            "documents",
            "analysis_jobs",
            "analysis_results",
            "document_summaries",
            "refresh_tokens",
        ):
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

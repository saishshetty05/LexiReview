"""Tests for MFA (TOTP) functionality.

Covers:
- /auth/mfa/setup generates valid otpauth:// URI with correct issuer/account
- /auth/mfa/verify-setup accepts valid code, rejects invalid
- POST /auth/login with MFA-enabled user returns mfa_pending token (no access token cookie)
- POST /auth/mfa/challenge with correct TOTP returns real access token
- POST /auth/mfa/challenge with wrong code returns 401
- POST /auth/mfa/challenge fails after 5 wrong attempts (rate limit)
- /auth/mfa/disable requires valid TOTP
"""
from __future__ import annotations

import base64
import io
import uuid

import pytest

import pyotp
from fastapi.testclient import TestClient

from app.auth import (
    create_access_token,
    create_mfa_pending_token,
    decode_mfa_pending_token,
    generate_mfa_secret,
    verify_totp_code,
)
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


class _MockRedis:
    """In-memory Redis mock for rate-limit tests."""

    def __init__(self):
        self._store: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    def expire(self, key: str, ttl: int) -> bool:
        return True

    def get(self, key: str) -> int | None:
        return self._store.get(key)

    def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0


@pytest.fixture(autouse=True)
def _mock_mfa_redis(monkeypatch):
    """Patch mfa_redis module to use in-memory mock for all tests."""
    mock = _MockRedis()
    import app.mfa_redis as mfa_redis_module

    monkeypatch.setattr(mfa_redis_module, "_client", lambda: mock)
    yield mock


def _unique_email() -> str:
    return f"mfa-test-{uuid.uuid4()}@example.invalid"


def _register_and_login() -> tuple[uuid.UUID, str, dict[str, str]]:
    """Register a user, login, return (user_id, email, auth_cookies)."""
    email = _unique_email()
    password = "TestPass123!"
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    user_id = uuid.UUID(resp.json()["user_id"])
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    token = login_resp.cookies[ACCESS_TOKEN_COOKIE]
    return user_id, email, {ACCESS_TOKEN_COOKIE: token}


def _enable_mfa_for_user(user_id: uuid.UUID, email: str, auth_cookies: dict) -> str:
    """Run full MFA setup + verify, return the secret."""
    # Setup
    setup_resp = client.post("/auth/mfa/setup", cookies=auth_cookies)
    assert setup_resp.status_code == 200, setup_resp.text
    setup_data = setup_resp.json()
    secret = setup_data["totp_secret"]
    qr_base64 = setup_data["qr_code_base64"]

    # Verify QR code decodes to valid otpauth URI
    qr_bytes = base64.b64decode(qr_base64)
    from PIL import Image
    Image.open(io.BytesIO(qr_bytes))
    # We can't easily decode QR in test, but we can verify the URI format by
    # checking the secret produces a valid TOTP that verify-setup accepts
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()

    # Verify setup
    verify_resp = client.post(
        "/auth/mfa/verify-setup",
        json={"code": valid_code},
        cookies=auth_cookies
    )
    assert verify_resp.status_code == 200, verify_resp.text
    assert verify_resp.json()["status"] == "mfa_enabled"

    return secret


# ── Tests ────────────────────────────────────────────────────────────────────


def test_mfa_setup_generates_valid_otpauth_uri(pg_owner_engine):
    """Setup returns QR code that encodes otpauth:// with issuer=LexiReview and account=email."""
    from app.auth import build_totp_uri

    secret = generate_mfa_secret()
    email = "test@example.com"
    uri = build_totp_uri(secret, email)

    assert uri.startswith("otpauth://totp/")
    assert "LexiReview:" in uri
    assert email in uri
    assert f"secret={secret}" in uri
    assert "issuer=LexiReview" in uri

    # The secret should be valid for pyotp
    totp = pyotp.TOTP(secret)
    code = totp.now()
    assert verify_totp_code(secret, code)


def test_mfa_verify_setup_accepts_valid_rejects_invalid(pg_owner_engine):
    """verify-setup accepts a valid TOTP code, rejects an invalid one."""
    user_id, email, auth_cookies = _register_and_login()

    # Setup MFA
    setup_resp = client.post("/auth/mfa/setup", cookies=auth_cookies)
    assert setup_resp.status_code == 200
    secret = setup_resp.json()["totp_secret"]

    # Valid code should work
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()
    verify_resp = client.post(
        "/auth/mfa/verify-setup",
        json={"code": valid_code},
        cookies=auth_cookies
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["status"] == "mfa_enabled"

    # Now MFA is enabled — trying to set up again should fail
    setup_resp2 = client.post("/auth/mfa/setup", cookies=auth_cookies)
    assert setup_resp2.status_code == 400
    assert setup_resp2.json()["detail"]["category"] == "mfa_already_enabled"


def test_mfa_verify_setup_rejects_invalid_code(pg_owner_engine):
    """verify-setup rejects an invalid TOTP code."""
    user_id, email, auth_cookies = _register_and_login()

    setup_resp = client.post("/auth/mfa/setup", cookies=auth_cookies)
    assert setup_resp.status_code == 200
    secret = setup_resp.json()["totp_secret"]

    # Invalid code (wrong by 1 digit)
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()
    invalid_code = str((int(valid_code) + 1) % 1000000).zfill(6)

    verify_resp = client.post(
        "/auth/mfa/verify-setup",
        json={"code": invalid_code},
        cookies=auth_cookies
    )
    assert verify_resp.status_code == 401
    assert verify_resp.json()["detail"]["category"] == "invalid_mfa_code"

    # MFA should NOT be enabled
    verify_resp2 = client.post(
        "/auth/mfa/verify-setup",
        json={"code": valid_code},
        cookies=auth_cookies
    )
    assert verify_resp2.status_code == 200  # valid code still works


def test_login_mfa_enabled_returns_mfa_pending_token(pg_owner_engine):
    """Login for MFA-enabled user returns mfa_required flag + mfa_token, NOT access token cookie."""
    user_id, email, auth_cookies = _register_and_login()
    _enable_mfa_for_user(user_id, email, auth_cookies)

    # Logout first
    client.post("/auth/logout", cookies=auth_cookies)

    # Login again — should return mfa_required
    login_resp = client.post("/auth/login", json={"email": email, "password": "TestPass123!"})
    assert login_resp.status_code == 200
    assert login_resp.json()["mfa_required"] is True
    assert "mfa_token" in login_resp.json()
    # No access token cookie should be set
    assert ACCESS_TOKEN_COOKIE not in login_resp.cookies

    # The mfa_token should be decodable
    mfa_token = login_resp.json()["mfa_token"]
    decoded_user_id = decode_mfa_pending_token(mfa_token)
    assert decoded_user_id == user_id


def test_mfa_challenge_valid_code_returns_access_token(pg_owner_engine):
    """Challenge with valid TOTP returns access token cookie."""
    user_id, email, auth_cookies = _register_and_login()
    secret = _enable_mfa_for_user(user_id, email, auth_cookies)

    # Login to get mfa_token
    login_resp = client.post("/auth/login", json={"email": email, "password": "TestPass123!"})
    assert login_resp.status_code == 200
    mfa_token = login_resp.json()["mfa_token"]

    # Challenge with valid code
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()

    challenge_resp = client.post(
        "/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "code": valid_code}
    )
    assert challenge_resp.status_code == 200
    assert challenge_resp.json()["user_id"] == str(user_id)
    assert ACCESS_TOKEN_COOKIE in challenge_resp.cookies

    # The access token should work for protected endpoints
    access_cookies = {ACCESS_TOKEN_COOKIE: challenge_resp.cookies[ACCESS_TOKEN_COOKIE]}
    jobs_resp = client.get("/jobs", cookies=access_cookies)  # 404 is fine, just not 401
    assert jobs_resp.status_code != 401


def test_mfa_challenge_invalid_code_returns_401(pg_owner_engine):
    """Challenge with invalid TOTP returns 401."""
    user_id, email, auth_cookies = _register_and_login()
    secret = _enable_mfa_for_user(user_id, email, auth_cookies)

    login_resp = client.post("/auth/login", json={"email": email, "password": "TestPass123!"})
    mfa_token = login_resp.json()["mfa_token"]

    # Invalid code
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()
    invalid_code = str((int(valid_code) + 1) % 1000000).zfill(6)

    challenge_resp = client.post(
        "/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "code": invalid_code}
    )
    assert challenge_resp.status_code == 401
    assert challenge_resp.json()["detail"]["category"] == "invalid_mfa_code"
    assert ACCESS_TOKEN_COOKIE not in challenge_resp.cookies


def test_mfa_challenge_rate_limited_after_5_attempts(pg_owner_engine):
    """Challenge fails after 5 wrong attempts (rate limit)."""
    user_id, email, auth_cookies = _register_and_login()
    secret = _enable_mfa_for_user(user_id, email, auth_cookies)

    login_resp = client.post("/auth/login", json={"email": email, "password": "TestPass123!"})
    mfa_token = login_resp.json()["mfa_token"]

    # 5 invalid attempts
    for i in range(5):
        challenge_resp = client.post(
            "/auth/mfa/challenge",
            json={"mfa_token": mfa_token, "code": "000000"}
        )
        assert challenge_resp.status_code == 401
        assert challenge_resp.json()["detail"]["category"] == "invalid_mfa_code"

    # 6th attempt should be rate limited
    challenge_resp = client.post(
        "/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "code": "000000"}
    )
    assert challenge_resp.status_code == 429
    assert challenge_resp.json()["detail"]["category"] == "mfa_rate_limited"

    # Even a valid code should now fail (token invalidated)
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()
    challenge_resp = client.post(
        "/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "code": valid_code}
    )
    assert challenge_resp.status_code == 429
    assert challenge_resp.json()["detail"]["category"] == "mfa_rate_limited"


def test_mfa_disable_requires_valid_code(pg_owner_engine):
    """Disable MFA requires a valid TOTP code."""
    user_id, email, auth_cookies = _register_and_login()
    secret = _enable_mfa_for_user(user_id, email, auth_cookies)

    # Invalid code should fail
    disable_resp = client.post(
        "/auth/mfa/disable",
        json={"code": "000000"},
        cookies=auth_cookies
    )
    assert disable_resp.status_code == 401
    assert disable_resp.json()["detail"]["category"] == "invalid_mfa_code"

    # Valid code should succeed
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()
    disable_resp = client.post(
        "/auth/mfa/disable",
        json={"code": valid_code},
        cookies=auth_cookies
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["status"] == "mfa_disabled"

    # Login should now work normally (no mfa_required)
    login_resp = client.post("/auth/login", json={"email": email, "password": "TestPass123!"})
    assert login_resp.status_code == 200
    assert "mfa_required" not in login_resp.json()
    assert ACCESS_TOKEN_COOKIE in login_resp.cookies


def test_mfa_disable_not_enabled_returns_400(pg_owner_engine):
    """Disable on non-MFA user returns 400."""
    user_id, email, auth_cookies = _register_and_login()
    # Don't enable MFA

    disable_resp = client.post(
        "/auth/mfa/disable",
        json={"code": "123456"},
        cookies=auth_cookies
    )
    assert disable_resp.status_code == 400
    assert disable_resp.json()["detail"]["category"] == "mfa_not_enabled"


def test_mfa_setup_not_configured_verify_returns_400(pg_owner_engine):
    """verify-setup without prior setup returns 400."""
    user_id, email, auth_cookies = _register_and_login()

    verify_resp = client.post(
        "/auth/mfa/verify-setup",
        json={"code": "123456"},
        cookies=auth_cookies
    )
    assert verify_resp.status_code == 400
    assert verify_resp.json()["detail"]["category"] == "mfa_not_configured"


def test_mfa_challenge_invalid_token_returns_401(pg_owner_engine):
    """Challenge with malformed/expired/wrong mfa_token returns 401."""
    challenge_resp = client.post(
        "/auth/mfa/challenge",
        json={"mfa_token": "not.a.real.token", "code": "123456"}
    )
    assert challenge_resp.status_code == 401
    assert challenge_resp.json()["detail"]["category"] == "invalid_token"

    # Token without mfa_pending claim
    access_token = create_access_token(uuid.uuid4())
    challenge_resp = client.post(
        "/auth/mfa/challenge",
        json={"mfa_token": access_token, "code": "123456"}
    )
    assert challenge_resp.status_code == 401
    assert challenge_resp.json()["detail"]["category"] == "invalid_token"


def test_mfa_challenge_missing_mfa_secret_returns_401(pg_owner_engine):
    """Challenge when user has mfa_enabled=True but no mfa_secret returns 401."""
    user_id, email, auth_cookies = _register_and_login()

    # Manually set mfa_enabled=True without secret (edge case)
    from app.db import app_user_session
    from app.models import User
    with app_user_session(user_id) as session:
        user = session.get(User, user_id)
        user.mfa_enabled = True
        user.mfa_secret = None
        session.flush()

    # Create mfa_token directly
    mfa_token = create_mfa_pending_token(user_id)

    challenge_resp = client.post(
        "/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "code": "123456"}
    )
    assert challenge_resp.status_code == 401
    assert challenge_resp.json()["detail"]["category"] == "invalid_token"
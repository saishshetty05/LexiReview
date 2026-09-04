"""GET /auth/me tests (CONTRACTS.md §3(c), v1.16).

End-to-end over the real app (app.main.app) against real Postgres, same
pattern as test_admin_users_api.py: users are created via app.auth.register
and the access JWT is minted directly (create_access_token), admin
promotion is a direct owner-engine UPDATE (there is no self-serve
promotion endpoint).
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


def _new_user(pg_owner_engine, *, is_admin: bool = False) -> dict:
    email = f"auth-me-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    user_id = user.id
    if is_admin:
        with pg_owner_engine.connect() as conn:
            conn.execute(text("UPDATE users SET is_admin = true WHERE id = :id"), {"id": user_id})
            conn.commit()
    token = create_access_token(user_id)
    return {"id": user_id, "email": email, "cookie": {ACCESS_TOKEN_COOKIE: token}}


def _cleanup(pg_owner_engine, *user_ids: uuid.UUID) -> None:
    with pg_owner_engine.connect() as conn:
        for uid in user_ids:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
        conn.commit()


def test_auth_me_returns_own_identity(pg_owner_engine):
    user = _new_user(pg_owner_engine)
    try:
        resp = client.get("/auth/me", cookies=user["cookie"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"user_id": str(user["id"]), "email": user["email"], "is_admin": False}
    finally:
        _cleanup(pg_owner_engine, user["id"])


def test_auth_me_reflects_admin_flag(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    try:
        resp = client.get("/auth/me", cookies=admin["cookie"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_admin"] is True
    finally:
        _cleanup(pg_owner_engine, admin["id"])


def test_auth_me_requires_auth():
    assert client.get("/auth/me").status_code == 401


def test_auth_me_no_secrets_leaked(pg_owner_engine):
    """Metadata only, per CLAUDE.md rule 2 -- password_hash/mfa_secret must
    never appear in the response, mirroring the safe-column contract §10
    already enforces for admin_list_users()."""
    user = _new_user(pg_owner_engine)
    try:
        resp = client.get("/auth/me", cookies=user["cookie"])
        assert set(resp.json().keys()) == {"user_id", "email", "is_admin"}
    finally:
        _cleanup(pg_owner_engine, user["id"])

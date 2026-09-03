"""Admin authentication enforcement tests (PR #2 of 3).

Cover two behaviors added in this PR:
1. `get_current_user` now rejects a suspended account (active=FALSE) with 403
   account_suspended — the single enforcement point where an admin's
   suspension takes effect on the target's next request.
2. `require_admin` (chaining on get_current_user) rejects a non-admin with
   403 admin_required.

We don't want to add throwaway endpoints to app.main for tests (the real
/admin/users endpoints land in PR #3), so this file spins up a throwaway
FastAPI app that reuses the REAL get_current_user / require_admin from
app.main. Users are seeded directly as owner (bypassing RLS — there is no
self-serve promotion endpoint by design; the first admin is a manual DB
bootstrap), and the access JWT is minted directly rather than tilting through
login (login itself is covered by test_auth.py). The production app object is
never mutated.

The core scenarios prove the no-JWT-claim design: promote/suspend/demote by
editing the users row in the DB mid-test keeps the SAME cookie, yet the next
request reflects the change — because get_current_user loads the User row fresh
every request instead of trusting anything in the token.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_access_token
from app.main import ACCESS_TOKEN_COOKIE, get_current_user, require_admin

# JWT minting + decoding need the same secret. Default so a bare local run (no
# .env sourced) still works — same fallback test_auth.py uses.
_JWT_SECRET = os.environ.get("JWT_SECRET", "test-secret")


def _build_app() -> FastAPI:
    a = FastAPI(title="admin-auth-test")

    @a.get("/me", dependencies=[Depends(get_current_user)])
    def me(current=Depends(get_current_user)):
        user, _session = current
        return {"user_id": str(user.id), "is_admin": user.is_admin, "active": user.active}

    @a.get("/admin-ping", dependencies=[Depends(require_admin)])
    def admin_ping(current=Depends(require_admin)):
        user, _session = current
        return {"user_id": str(user.id), "is_admin": user.is_admin}

    return a


@pytest.fixture()
def ctx(pg_owner_engine, monkeypatch):
    """A TestClient (throwaway app) + a fresh user seeded directly via owner.

    Yields a (client, user_id) tuple. The user starts is_admin=FALSE,
    active=TRUE; each test promotes/suspends via `set_user` as needed.
    """
    monkeypatch.setenv("JWT_SECRET", _JWT_SECRET)
    app = _build_app()
    c = TestClient(app)
    user_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_admin, active, created_at) "
                "VALUES (:id, :email, 'x', false, true, now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
        conn.commit()
    yield c, user_id
    with pg_owner_engine.connect() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        conn.commit()


def set_user(pg_owner_engine, user_id: uuid.UUID, *, is_admin: bool, active: bool) -> None:
    """Promote/suspend a user directly in the DB (as owner, bypasses RLS). No
    self-serve promotion endpoint exists — first admin is a manual bootstrap.
    """
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("UPDATE users SET is_admin = :ia, active = :act WHERE id = :id"),
            {"ia": is_admin, "act": active, "id": user_id},
        )
        conn.commit()


def login(c: TestClient, user_id: uuid.UUID) -> None:
    """Mint an access JWT for the user and attach it as the real cookie — the
    same cookie get_current_user reads. The token is static; all role/active
    changes happen in the DB, so a mid-test DB edit must still be reflected by
    the NEXT request (that is exactly what we assert).
    """
    token = create_access_token(user_id)
    c.cookies.set(ACCESS_TOKEN_COOKIE, token)


# ── get_current_user: suspended account ────────────────────────────────────
def test_protected_endpoint_rejects_suspended_user(ctx, pg_owner_engine):
    c, user_id = ctx
    set_user(pg_owner_engine, user_id, is_admin=False, active=True)
    login(c, user_id)

    assert c.get("/me").status_code == 200

    # Suspend: the SAME token is now rejected with 403 — active is read from
    # the DB each request, not from anything in the JWT issued at login.
    set_user(pg_owner_engine, user_id, is_admin=False, active=False)
    resp = c.get("/me")
    assert resp.status_code == 403
    assert resp.json()["detail"]["category"] == "account_suspended"


# ── require_admin: non-admin / admin ───────────────────────────────────────
def test_require_admin_rejects_non_admin(ctx, pg_owner_engine):
    c, user_id = ctx
    set_user(pg_owner_engine, user_id, is_admin=False, active=True)
    login(c, user_id)

    resp = c.get("/admin-ping")
    assert resp.status_code == 403
    assert resp.json()["detail"]["category"] == "admin_required"


def test_require_admin_allows_admin(ctx, pg_owner_engine):
    c, user_id = ctx
    set_user(pg_owner_engine, user_id, is_admin=True, active=True)
    login(c, user_id)

    resp = c.get("/admin-ping")
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


def test_require_admin_requires_authentication(ctx):
    """A caller with no cookie gets 401 from the chained get_current_user
    before require_admin's admin check even runs.
    """
    c, _user_id = ctx
    resp = c.get("/admin-ping")
    assert resp.status_code == 401
    assert resp.json()["detail"]["category"] == "missing_token"


def test_require_admin_rejects_suspended_admin(ctx, pg_owner_engine):
    """Suspending an admin revokes their admin access too — the active check in
    get_current_user (which require_admin chains on) fires before the admin
    check.
    """
    c, user_id = ctx
    set_user(pg_owner_engine, user_id, is_admin=True, active=True)
    login(c, user_id)
    assert c.get("/admin-ping").status_code == 200

    set_user(pg_owner_engine, user_id, is_admin=True, active=False)
    resp = c.get("/admin-ping")
    assert resp.status_code == 403
    assert resp.json()["detail"]["category"] == "account_suspended"


# ── Demotion takes effect immediately (no JWT admin claim) ─────────────────
def test_demoted_admin_loses_access_on_next_request(ctx, pg_owner_engine):
    """The whole reason there's no admin JWT claim: demoting an admin takes
    effect on their very next request, with the SAME token.
    """
    c, user_id = ctx
    set_user(pg_owner_engine, user_id, is_admin=True, active=True)
    login(c, user_id)
    assert c.get("/admin-ping").status_code == 200

    set_user(pg_owner_engine, user_id, is_admin=False, active=True)
    resp = c.get("/admin-ping")
    assert resp.status_code == 403
    assert resp.json()["detail"]["category"] == "admin_required"

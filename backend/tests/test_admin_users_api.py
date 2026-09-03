"""Admin /admin/users API tests (CONTRACTS.md §10; PR #3 of 3).

End-to-end over the real app (app.main.app) against real Postgres — these
exercise get_current_user's active-check + require_admin + the SECURITY
DEFINER functions from migration 013 all the way through the HTTP layer.

Auth follows test_jobs_api's pattern: users are created via app.auth.register
and the access JWT is minted directly (create_access_token) rather than a login
round-trip, so these tests don't touch the login rate limiter. There is no
self-serve admin promotion (by design — the first admin is a manual DB
bootstrap), so promotion is done here with an owner-engine UPDATE, exactly the
way a real deployment bootstraps admins.

Several tests assert that the SAME cached token is invalidated by a DB change
made after it was minted (mid-test UPDATE to users.active / users.is_admin):
that is the whole point of the no-JWT-claim design — get_current_user loads the
row fresh every request, so there is no admin/suspension state to revoke or to
paper over.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


def _new_user(pg_owner_engine, *, is_admin: bool = False) -> dict:
    """Register a user, optionally promoting them to admin via the owner engine
    (the DB-level bootstrap a real deployment does manually — there is no
    self-serve promotion endpoint), and mint an access JWT for them.

    Returns {"id", "email", "cookie"} where cookie is a per-request cookie dict.
    """
    email = f"admin-api-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    user_id = user.id
    if is_admin:
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text("UPDATE users SET is_admin = true WHERE id = :id"), {"id": user_id}
            )
            conn.commit()
    token = create_access_token(user_id)
    return {"id": user_id, "email": email, "cookie": {ACCESS_TOKEN_COOKIE: token}}


def _cleanup(pg_owner_engine, *user_ids: uuid.UUID) -> None:
    """Delete test users. audit_log.user_id is an FK to users, so audit rows
    referencing the users must go first; target_user_id has no FK (migration
    012) so it can't block the delete, but clearing it keeps the table tidy.
    """
    with pg_owner_engine.connect() as conn:
        for uid in user_ids:
            conn.execute(
                text("DELETE FROM audit_log WHERE user_id = :id OR target_user_id = :id"),
                {"id": uid},
            )
        for uid in user_ids:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
        conn.commit()


def _active_in_db(pg_owner_engine, user_id: uuid.UUID) -> bool:
    with pg_owner_engine.connect() as conn:
        (active,) = conn.execute(
            text("SELECT active FROM users WHERE id = :id"), {"id": user_id}
        ).fetchone()
    return active


def _list_rows(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── GET /admin/users ──────────────────────────────────────────────────────
def test_admin_can_list_all_users(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    alice = _new_user(pg_owner_engine)
    bob = _new_user(pg_owner_engine)
    try:
        rows = _list_rows(client.get("/admin/users", cookies=admin["cookie"]))

        # Every seeded user is visible (cross-user read via SECURITY DEFINER).
        emails = {r["email"] for r in rows}
        assert {admin["email"], alice["email"], bob["email"]} <= emails

        # Metadata only — the safe-column contract lives in the DB function.
        by_email = {r["email"]: r for r in rows}
        for r in rows:
            assert set(r.keys()) == {"user_id", "email", "is_admin", "active", "created_at"}
            assert "password_hash" not in r and "mfa_secret" not in r
            assert "T" in r["created_at"]  # ISO-8601 datetime, not a raw object
        assert by_email[admin["email"]]["is_admin"] is True
        assert by_email[alice["email"]]["is_admin"] is False
        assert by_email[bob["email"]]["active"] is True
    finally:
        _cleanup(pg_owner_engine, admin["id"], alice["id"], bob["id"])


def test_list_users_requires_admin(pg_owner_engine):
    normal = _new_user(pg_owner_engine)
    try:
        resp = client.get("/admin/users", cookies=normal["cookie"])
        assert resp.status_code == 403
        assert resp.json()["detail"]["category"] == "admin_required"
        # No cookie at all → 401 from the chained get_current_user.
        assert client.get("/admin/users").status_code == 401
    finally:
        _cleanup(pg_owner_engine, normal["id"])


def test_suspended_admin_cannot_list(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    try:
        assert client.get("/admin/users", cookies=admin["cookie"]).status_code == 200
        # Suspend the admin directly in the DB — the SAME token is now rejected,
        # because get_current_user loads active fresh each request.
        with pg_owner_engine.connect() as conn:
            conn.execute(
                text("UPDATE users SET active = false WHERE id = :id"), {"id": admin["id"]}
            )
            conn.commit()
        resp = client.get("/admin/users", cookies=admin["cookie"])
        assert resp.status_code == 403
        assert resp.json()["detail"]["category"] == "account_suspended"
    finally:
        _cleanup(pg_owner_engine, admin["id"])


# ── PATCH /admin/users/{id} ───────────────────────────────────────────────
def test_admin_can_suspend_and_reactivate_target(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    target = _new_user(pg_owner_engine)
    try:
        # Baseline: the target can make protected calls.
        assert client.get("/documents", cookies=target["cookie"]).status_code == 200

        # Suspend.
        resp = client.patch(
            f"/admin/users/{target['id']}", json={"active": False}, cookies=admin["cookie"]
        )
        assert resp.status_code == 200
        assert resp.json() == {"user_id": str(target["id"]), "active": False}
        assert _active_in_db(pg_owner_engine, target["id"]) is False

        # The SAME cached token is now rejected on the target's next request —
        # get_current_user sees active=False from the DB; no token revocation
        # was signaled or needed.
        resp = client.get("/documents", cookies=target["cookie"])
        assert resp.status_code == 403
        assert resp.json()["detail"]["category"] == "account_suspended"

        # Reactivate.
        resp = client.patch(
            f"/admin/users/{target['id']}", json={"active": True}, cookies=admin["cookie"]
        )
        assert resp.status_code == 200
        assert _active_in_db(pg_owner_engine, target["id"]) is True
        assert client.get("/documents", cookies=target["cookie"]).status_code == 200
    finally:
        _cleanup(pg_owner_engine, admin["id"], target["id"])


def test_suspend_requires_admin(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    normal = _new_user(pg_owner_engine)
    try:
        # A non-admin cannot suspend anyone.
        resp = client.patch(
            f"/admin/users/{normal['id']}", json={"active": False}, cookies=normal["cookie"]
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["category"] == "admin_required"
        # No cookie → 401.
        resp = client.patch(f"/admin/users/{normal['id']}", json={"active": False})
        assert resp.status_code == 401
    finally:
        _cleanup(pg_owner_engine, admin["id"], normal["id"])


def test_admin_cannot_suspend_self(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    try:
        resp = client.patch(
            f"/admin/users/{admin['id']}", json={"active": False}, cookies=admin["cookie"]
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["category"] == "cannot_self_suspend"
        # Fail-closed: the admin stays active.
        assert _active_in_db(pg_owner_engine, admin["id"]) is True
    finally:
        _cleanup(pg_owner_engine, admin["id"])


def test_suspend_nonexistent_user_404(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    try:
        resp = client.patch(
            f"/admin/users/{uuid.uuid4()}", json={"active": False}, cookies=admin["cookie"]
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["category"] == "user_not_found"
    finally:
        _cleanup(pg_owner_engine, admin["id"])


# ── Audit trail ───────────────────────────────────────────────────────────
def test_suspend_writes_audit_log_row(pg_owner_engine):
    admin = _new_user(pg_owner_engine, is_admin=True)
    target = _new_user(pg_owner_engine)
    try:
        resp = client.patch(
            f"/admin/users/{target['id']}", json={"active": False}, cookies=admin["cookie"]
        )
        assert resp.status_code == 200

        with pg_owner_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT event_type, user_id, target_user_id FROM audit_log "
                    "WHERE target_user_id = :tid ORDER BY created_at DESC"
                ),
                {"tid": target["id"]},
            ).fetchall()
        # metadata only (CLAUDE.md rule 2): actor + event + target, no content.
        assert len(rows) == 1
        assert rows[0].event_type == "user_suspended"
        assert rows[0].user_id == admin["id"]
        assert rows[0].target_user_id == target["id"]
    finally:
        _cleanup(pg_owner_engine, admin["id"], target["id"])

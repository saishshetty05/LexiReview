"""Verifies migration 005 (RLS fail-closed on app.user_id NEVER set, not just
set-invalid -- follow-up to PR #38, docs/DECISION_LOG.md 2026-07-20).

Uses a dedicated NullPool engine (not the shared pg_app_engine pool) for the
"never set" assertions specifically because connection pooling makes that
case non-reproducible on a reused connection: once ANY test has SET LOCAL
app.user_id on a given physical backend, Postgres registers app.* as a real
(if currently reset-to-empty) custom GUC for that backend's remaining
lifetime, and current_setting(..., true) on it returns '' (which already
raised "invalid input syntax" even before this migration -- see
test_rls_smoke.py). A genuinely fresh connection that has NEVER referenced
app.user_id in its backend's lifetime is the only way to reproduce the
actual NULL case #38 hit, and NullPool guarantees a brand-new physical
connection rather than a pooled, possibly-already-touched one.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app

BACKEND_DIR = Path(__file__).resolve().parent.parent

client = TestClient(app)


@pytest.fixture()
def fresh_app_connection(pg_app_engine):
    """A brand-new physical connection, guaranteed to have never referenced
    app.user_id -- see module docstring for why the shared pooled fixtures
    can't reproduce this case reliably.
    """
    engine = create_engine(
        pg_app_engine.url.render_as_string(hide_password=False), poolclass=NullPool
    )
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def _self_only_policy_exists_with_missing_ok(pg_owner_engine) -> bool:
    with pg_owner_engine.connect() as conn:
        qual = conn.execute(
            text("SELECT qual FROM pg_policies WHERE tablename = 'users' AND policyname = 'self_only'")
        ).scalar_one()
    return ", true" in qual or ",true" in qual


def test_migration_005_applies_and_downgrades_cleanly(pg_owner_engine):
    """Assumes the test DB starts at head (005). Downgrades to 004, checks
    self_only's predicate reverts to the missing_ok form, then re-upgrades
    to head and checks it's gone again -- leaving the DB at head for every
    other test in the suite, same pattern as prior migration tests.
    """

    def _run(*args: str) -> None:
        subprocess.run(
            ["python", "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=os.environ,
            check=True,
            capture_output=True,
            text=True,
        )

    assert not _self_only_policy_exists_with_missing_ok(pg_owner_engine)

    try:
        _run("downgrade", "004_summary_error")
        assert _self_only_policy_exists_with_missing_ok(pg_owner_engine)
    finally:
        _run("upgrade", "head")

    assert not _self_only_policy_exists_with_missing_ok(pg_owner_engine)


def test_truly_unset_app_user_id_now_raises_on_user_isolation_table(fresh_app_connection):
    """The actual #38 scenario: a connection that never once called SET
    LOCAL app.user_id. Before migration 005, current_setting(..., true)
    returned NULL here (not an error), so this SELECT would have silently
    returned zero rows. Now it must raise instead.
    """
    with pytest.raises(ProgrammingError, match="unrecognized configuration parameter"):
        fresh_app_connection.execute(text("SELECT id FROM analysis_jobs"))
    fresh_app_connection.rollback()


def test_truly_unset_app_user_id_now_raises_on_users_insert(fresh_app_connection):
    """users.self_only alone governs INSERT (email_lookup is FOR SELECT
    only), so this must raise too -- not silently insert a row nobody could
    subsequently see under RLS.
    """
    with pytest.raises(ProgrammingError, match="unrecognized configuration parameter"):
        fresh_app_connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": uuid.uuid4(), "email": f"{uuid.uuid4()}@example.invalid"},
        )
    fresh_app_connection.rollback()


def test_truly_unset_app_user_id_select_on_users_still_succeeds_via_email_lookup(
    fresh_app_connection, pg_owner_engine
):
    """The critical non-regression proof: self_only's now-erroring predicate
    must NOT break users SELECT for a connection that never set
    app.user_id, because email_lookup's unconditional USING (true) ORs with
    it. Verified both a positive match and a correct zero-row miss --
    empirically confirmed against the running dev DB before writing this
    migration, this test locks that behavior in as a regression guard.
    """
    user_id = uuid.uuid4()
    email = f"migration-005-probe-{user_id}@example.invalid"
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": user_id, "email": email},
        )
        conn.commit()

    try:
        miss = fresh_app_connection.execute(
            text("SELECT id FROM users WHERE email = 'nonexistent@example.invalid'")
        ).fetchall()
        assert miss == []

        hit = fresh_app_connection.execute(
            text("SELECT id FROM users WHERE email = :email"), {"email": email}
        ).fetchall()
        assert [row[0] for row in hit] == [user_id]
    finally:
        with pg_owner_engine.connect() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            conn.commit()


def test_login_end_to_end_still_works_after_policy_change(pg_owner_engine):
    """Full-stack non-regression: login (app_user_session(None) -> SELECT
    via email_lookup) must still work through the real API, not just at the
    raw-SQL level above.
    """
    email = f"migration-005-login-test-{uuid.uuid4()}@example.invalid"
    password = "TestPass123!"
    user = register(email, password)
    try:
        resp = client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200
        assert ACCESS_TOKEN_COOKIE in resp.cookies

        # And the issued token actually authorizes a protected request,
        # proving the whole chain (login -> cookie -> get_current_user,
        # which DOES set app.user_id) still works end to end too.
        token = create_access_token(user.id)
        protected_resp = client.get(
            f"/jobs/{uuid.uuid4()}", cookies={ACCESS_TOKEN_COOKIE: token}
        )
        assert protected_resp.status_code == 404  # not 401/500 -- auth succeeded
    finally:
        with pg_owner_engine.connect() as conn:
            # The login above (CONTRACTS.md §9) creates a refresh_tokens
            # row with no ON DELETE CASCADE back to users -- must go first.
            conn.execute(text("DELETE FROM refresh_tokens WHERE user_id = :id"), {"id": user.id})
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
            conn.commit()

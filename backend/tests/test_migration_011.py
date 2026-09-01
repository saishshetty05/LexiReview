"""Migration 011 (outbox table + dedicated relay role) verification.

Assumes `alembic upgrade head` has already been run against DATABASE_URL — CI
does this in its own step before pytest; locally, run it yourself first (see
backend/tests/conftest.py). Uses the owner engine for schema/role introspection,
plus app_user (write path) and the new relay engine (the sweeper) for the
functional cross-user and CASCADE checks.

Access model recap (see the migration docstring, the signed
docs/MIGRATION_011_RLS_DECISION.md, and DECISION_LOG.md 2026-09-01): outbox has
NO RLS by design — the relay sweeps `delivered_at IS NULL` across all users and
the locked contract gives it no user_id column, so grants-only is the only
workable access control. app_user gets INSERT on outbox only; the relay role
gets SELECT+UPDATE on outbox only, and no grant on any user table.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

# Every user-scoped table the relay must have NO grant on.
USER_TABLES = (
    "users",
    "documents",
    "analysis_jobs",
    "analysis_results",
    "audit_log",
    "document_summaries",
    "decisions",
    "refresh_tokens",
)


# ── 1. Columns / constraints (owner) ──────────────────────────────────────
def test_outbox_columns_and_constraints(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        table_rows = conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'outbox'"
            )
        ).fetchall()
        fk_rows = conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'outbox'::regclass AND contype IN ('f', 'u')"
            )
        ).fetchall()

    columns = {r[0]: r[1] for r in table_rows}
    assert columns["id"] == "NO"
    assert columns["job_id"] == "NO"
    assert columns["payload_json"] == "NO"
    assert columns["attempts"] == "NO"
    assert columns["last_attempt_at"] == "YES"
    assert columns["delivered_at"] == "YES"
    assert columns["created_at"] == "NO"

    # job_id FK -> analysis_jobs.id ON DELETE CASCADE.
    fk_text = " ".join(c[1] for c in fk_rows if c[0].startswith("outbox"))
    assert "FOREIGN KEY (job_id) REFERENCES analysis_jobs(id) ON DELETE CASCADE" in fk_text

    # job_id UNIQUE (one outbox row per analysis job, per CONTRACTS.md §1).
    assert any("UNIQUE (job_id)" in c[1] for c in fk_rows if c[0].startswith("outbox"))

    # attempts server_default '0' (reported as '0'::integer by information_schema).
    with pg_owner_engine.connect() as conn:
        attempts_default = conn.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'outbox' AND column_name = 'attempts'"
            )
        ).scalar()
    assert attempts_default is not None
    assert "0" in attempts_default


# ── 2. No RLS by design (owner) ───────────────────────────────────────────
def test_outbox_has_no_rls_by_design(pg_owner_engine):
    """The outbox is the deliberate exception to FORCE-RLS-everywhere: a
    cross-user sweeper can't be scoped by app.user_id, so access is grants-only.
    Lock the exception in so a future dev doesn't "fix" it into FORCE RLS and
    break the relay.
    """
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'outbox'"
            )
        ).fetchone()
    assert row is not None, "outbox table was not created by migration 011"
    rls_enabled, rls_forced = row
    assert rls_enabled is False, "outbox must NOT have RLS enabled (cross-user sweeper)"
    assert rls_forced is False, "outbox must NOT have FORCE RLS (would break the relay)"


# ── 3. Relay role flags (owner) ───────────────────────────────────────────
def test_relay_role_is_non_superuser_no_bypassrls(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'relay'")
        ).fetchone()
    assert row is not None, "relay role was not created by migration 011"
    rolsuper, rolbypassrls = row
    assert rolsuper is False, "relay must not be superuser (BYPASSRLS reserved for owner)"
    assert rolbypassrls is False, "relay must not have BYPASSRLS (least-privilege)"


# ── 4. Grants (owner) ─────────────────────────────────────────────────────
def test_grants_app_user_insert_and_relay_select_update_only(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT grantee, table_name, privilege_type "
                "FROM information_schema.role_table_grants "
                "WHERE table_name = 'outbox'"
            )
        ).fetchall()

    grants_by_role: dict[str, set[str]] = {}
    for grantee, _table, priv in rows:
        grants_by_role.setdefault(grantee, set()).add(priv)

    assert grants_by_role.get("app_user") == {"INSERT"}, (
        f"app_user: expected INSERT-only on outbox, got {grants_by_role.get('app_user')}"
    )
    assert grants_by_role.get("relay") == {"SELECT", "UPDATE"}, (
        f"relay: expected SELECT+UPDATE on outbox, got {grants_by_role.get('relay')}"
    )


def test_relay_has_no_grant_on_any_user_table(pg_owner_engine):
    """A compromised relay reads delivery metadata, never user data."""
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT table_name FROM information_schema.role_table_grants "
                "WHERE grantee = 'relay'"
            )
        ).fetchall()
    granted_tables = {r[0] for r in rows}
    assert granted_tables == {"outbox"}, (
        f"relay must have grants on outbox ONLY, got: {granted_tables}"
    )
    assert USER_TABLES.isdisjoint(granted_tables), (
        "relay must have no grant on any user table (least-privilege)"
    )


# ── 5. Functional cross-user sweep + scoping (all three roles) ────────────
@pytest.fixture()
def seeded_job_and_outbox(pg_owner_engine, pg_app_engine):
    """Seed a user + job as owner (owner bypasses RLS), then insert the outbox
    row as app_user (the API write path). Cleanup via owner for the user row
    (app_user has no DELETE on users by design); the outbox row is removed
    implicitly when CASCADE deletes the job below.
    """
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:id, :email, 'x', now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, created_at) "
                "VALUES (:id, :uid, :doc_id, 'testhash', 'queued', now())"
            ),
            {"id": job_id, "uid": user_id, "doc_id": uuid.uuid4()},
        )
        conn.commit()

    # API write path: app_user inserts the outbox row (INSERT-only grant).
    with pg_app_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO outbox (id, job_id, payload_json, created_at) "
                "VALUES (:id, :job_id, :payload, now())"
            ),
            {"id": uuid.uuid4(), "job_id": job_id, "payload": {"kind": "analysis", "v": 1}},
        )
        conn.commit()

    yield user_id, job_id

    # Cleanup via owner (job + outbox): deleting the job cascades to outbox.
    with pg_owner_engine.connect() as conn:
        conn.execute(text("DELETE FROM analysis_jobs WHERE id = :id"), {"id": job_id})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        conn.commit()


def test_relay_cross_user_sweep_and_scoping(
    pg_owner_engine, pg_app_engine, pg_relay_engine, seeded_job_and_outbox
):
    user_id, job_id = seeded_job_and_outbox

    # Relay (no SET LOCAL app.user_id — it has no user context) sweeps the
    # outbox across all users: it must see this job's undelivered row.
    with pg_relay_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT job_id, attempts, delivered_at FROM outbox WHERE delivered_at IS NULL")
        ).fetchall()
    assert {r[0] for r in rows} == {job_id}

    # Relay advances the delivery: bump attempts, mark delivered.
    with pg_relay_engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE outbox SET attempts = attempts + 1, delivered_at = now() "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        )
        conn.commit()
    with pg_relay_engine.connect() as conn:
        (attempts,) = conn.execute(
            text("SELECT attempts FROM outbox WHERE job_id = :job_id"), {"job_id": job_id}
        ).fetchone()
    assert attempts == 1

    # Once delivered, the sweep no longer picks it up.
    with pg_relay_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT job_id FROM outbox WHERE delivered_at IS NULL")
        ).fetchall()
    assert rows == []

    # Relay must NOT be able to read any user table (least-privilege).
    with pg_relay_engine.connect() as conn:
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text("SELECT id, user_id FROM documents"))
        conn.rollback()

    # app_user must NOT be able to read (or update) the outbox — INSERT only.
    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text("SELECT id FROM outbox"))
        conn.rollback()


# ── 6. ON DELETE CASCADE (owner) ──────────────────────────────────────────
def test_deleting_job_cascades_to_outbox(pg_owner_engine, seeded_job_and_outbox):
    """Account deletion deletes a user's jobs (delete_account_cascade in
    auth.py) without a user_id column on outbox, so ON DELETE CASCADE is what
    sweeps up their outbox rows — the relay then never attempts delivery for a
    deleted user's job.
    """
    _user_id, job_id = seeded_job_and_outbox
    with pg_owner_engine.connect() as conn:
        # Sanity: the outbox row exists before the job is deleted.
        before = conn.execute(
            text("SELECT count(*) FROM outbox WHERE job_id = :job_id"), {"job_id": job_id}
        ).scalar()
        conn.execute(text("DELETE FROM analysis_jobs WHERE id = :id"), {"id": job_id})
        conn.commit()
        after = conn.execute(
            text("SELECT count(*) FROM outbox WHERE job_id = :job_id"), {"job_id": job_id}
        ).scalar()
    assert before == 1
    assert after == 0, "deleting analysis_job row must cascade-delete its outbox row"

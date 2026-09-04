"""Migration 014 (analysis_costs table) verification.

Assumes `alembic upgrade head` has already been run against DATABASE_URL --
CI does this in its own step before pytest; locally, run it yourself first
(see backend/tests/conftest.py).

Unlike outbox (migration 011, no RLS by design -- a cross-user sweeper
needs grants-only access), analysis_costs is a normal user-scoped table:
FORCE RLS, self_only-equivalent user_isolation policy, SELECT+INSERT-only
grant (immutable-artifact pattern, same as analysis_results).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, ProgrammingError


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


def _insert_job(pg_owner_engine, *, user_id: uuid.UUID, doc_id: uuid.UUID) -> uuid.UUID:
    job_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, retry_count, created_at) "
                "VALUES (:id, :uid, :doc_id, 'abc', 'queued', 0, now())"
            ),
            {"id": job_id, "uid": user_id, "doc_id": doc_id},
        )
        conn.commit()
    return job_id


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM analysis_costs WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(
            text("DELETE FROM analysis_jobs WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


# ── 1. Columns / constraints (owner) ──────────────────────────────────────
def test_analysis_costs_columns_and_constraints(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        table_rows = conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'analysis_costs'"
            )
        ).fetchall()
        fk_rows = conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'analysis_costs'::regclass AND contype = 'f'"
            )
        ).fetchall()
        index_rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'analysis_costs'"
            )
        ).fetchall()
        created_at_default = conn.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'analysis_costs' AND column_name = 'created_at'"
            )
        ).scalar()

    # §11's table spec says "default now()" (unlike most other tables' contract
    # wording, which says "set by API/worker on insert") -- a real DB-level
    # server_default, not just the ORM's Python-side default, so any future
    # non-ORM writer that omits created_at still gets the contract's promised
    # value instead of a NOT NULL violation.
    assert created_at_default is not None and "now()" in created_at_default

    columns = {r[0]: r[1] for r in table_rows}
    assert columns["id"] == "NO"
    assert columns["job_id"] == "NO"
    assert columns["user_id"] == "NO"
    assert columns["call_type"] == "NO"
    assert columns["model"] == "NO"
    assert columns["input_tokens"] == "NO"
    assert columns["output_tokens"] == "NO"
    assert columns["created_at"] == "NO"

    fk_text = " ".join(c[1] for c in fk_rows)
    assert "FOREIGN KEY (job_id) REFERENCES analysis_jobs(id)" in fk_text
    assert "FOREIGN KEY (user_id) REFERENCES users(id)" in fk_text

    # §11's Deferred note presupposes this index exists -- load-bearing,
    # since the quota count in upload_document runs on every upload.
    assert any(
        r.indexname == "ix_analysis_costs_user_id_created_at" for r in index_rows
    ), [r.indexname for r in index_rows]


# ── 2. RLS: forced, self-scoped ───────────────────────────────────────────
def test_analysis_costs_has_forced_rls(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'analysis_costs'"
            )
        ).fetchone()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


def test_analysis_costs_user_isolation_scopes_reads(pg_owner_engine, pg_app_engine, cleanup_rows):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    cleanup_rows.extend([user_a, user_b])
    _insert_user(pg_owner_engine, user_a)
    _insert_user(pg_owner_engine, user_b)
    job_a = _insert_job(pg_owner_engine, user_id=user_a, doc_id=doc_a)
    job_b = _insert_job(pg_owner_engine, user_id=user_b, doc_id=doc_b)

    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_costs "
                "(id, job_id, user_id, call_type, model, input_tokens, output_tokens, created_at) "
                "VALUES (:id, :job_id, :user_id, 'analyze', 'claude-test', 10, 5, now())"
            ),
            {"id": uuid.uuid4(), "job_id": job_a, "user_id": user_a},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_costs "
                "(id, job_id, user_id, call_type, model, input_tokens, output_tokens, created_at) "
                "VALUES (:id, :job_id, :user_id, 'analyze', 'claude-test', 20, 8, now())"
            ),
            {"id": uuid.uuid4(), "job_id": job_b, "user_id": user_b},
        )
        conn.commit()

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_a}'"))
        rows = conn.execute(text("SELECT input_tokens FROM analysis_costs")).fetchall()
        conn.rollback()

    assert [r.input_tokens for r in rows] == [10]


def test_analysis_costs_fails_closed_when_app_user_id_unset(pg_app_engine):
    """Post-005 fail-closed predicate: an app_user connection that never set
    app.user_id gets a hard DataError (''::uuid raises
    InvalidTextRepresentation), not a silently empty result -- same
    DELIBERATE fail-closed choice test_rls_smoke.py verifies for the other
    user-scoped tables."""
    with pg_app_engine.connect() as conn:
        with pytest.raises(DataError, match="invalid input syntax for type uuid"):
            conn.execute(text("SELECT * FROM analysis_costs")).fetchall()
        conn.rollback()


# ── 3. Grants: SELECT+INSERT only, immutable-artifact pattern ────────────
def test_analysis_costs_grants_are_select_insert_only(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_name = 'analysis_costs' AND grantee = 'app_user'"
            )
        ).fetchall()
    privileges = {r[0] for r in rows}
    assert privileges == {"SELECT", "INSERT"}


def test_analysis_costs_app_user_cannot_update_or_delete(
    pg_owner_engine, pg_app_engine, cleanup_rows
):
    user_id, doc_id = uuid.uuid4(), uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    job_id = _insert_job(pg_owner_engine, user_id=user_id, doc_id=doc_id)
    cost_id = uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_costs "
                "(id, job_id, user_id, call_type, model, input_tokens, output_tokens, created_at) "
                "VALUES (:id, :job_id, :user_id, 'analyze', 'claude-test', 10, 5, now())"
            ),
            {"id": cost_id, "job_id": job_id, "user_id": user_id},
        )
        conn.commit()

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        with pytest.raises(ProgrammingError):
            conn.execute(
                text("UPDATE analysis_costs SET input_tokens = 999 WHERE id = :id"),
                {"id": cost_id},
            )
        conn.rollback()

    with pg_app_engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
        with pytest.raises(ProgrammingError):
            conn.execute(text("DELETE FROM analysis_costs WHERE id = :id"), {"id": cost_id})
        conn.rollback()

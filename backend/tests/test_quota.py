"""Tests for app/quota.py — CONTRACTS.md §11 (v1.17), PRD FR-16.

Month-boundary logic (month_start_utc/next_month_start_utc) is pure and
tested standalone, with `now` injected explicitly rather than left to
`date_trunc('month', now())` in SQL -- the December -> January rollover and
month-start/end edges are then plain function calls, not something that
needs clock manipulation. check_quota's DB-touching count logic is tested
against real Postgres, same pattern as the rest of the suite.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.quota import (
    DEFAULT_ANALYSIS_QUOTA_MONTHLY,
    QuotaConfig,
    check_quota,
    month_start_utc,
    next_month_start_utc,
)


# ── Pure boundary logic ────────────────────────────────────────────────────


def test_month_start_utc_mid_month():
    now = datetime(2026, 9, 17, 14, 30, 5, tzinfo=timezone.utc)
    assert month_start_utc(now) == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_month_start_utc_last_second_of_month():
    now = datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)
    assert month_start_utc(now) == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_month_start_utc_first_second_of_month():
    now = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert month_start_utc(now) == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_next_month_start_utc_mid_year():
    now = datetime(2026, 9, 17, 14, 30, 5, tzinfo=timezone.utc)
    assert next_month_start_utc(now) == datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_next_month_start_utc_december_rollover():
    """The case that breaks a naive `month + 1` (overflows to month=13)."""
    now = datetime(2026, 12, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert next_month_start_utc(now) == datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_next_month_start_utc_last_second_of_december():
    now = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert next_month_start_utc(now) == datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_month_start_utc_converts_non_utc_input():
    """A naive/non-UTC datetime is normalized to UTC before truncation --
    upload_document always passes datetime.now(timezone.utc), but the
    function itself shouldn't silently misbehave if that ever changes."""
    from datetime import timedelta, timezone as tz

    ist = tz(timedelta(hours=5, minutes=30))
    # 2026-09-01 02:00 IST is still 2026-08-31 UTC.
    now = datetime(2026, 9, 1, 2, 0, 0, tzinfo=ist)
    assert month_start_utc(now) == datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)


# ── QuotaConfig ─────────────────────────────────────────────────────────────


def test_quota_config_defaults_to_200(monkeypatch):
    monkeypatch.delenv("ANALYSIS_QUOTA_MONTHLY", raising=False)
    assert QuotaConfig.from_env().monthly_limit == DEFAULT_ANALYSIS_QUOTA_MONTHLY == 200


def test_quota_config_reads_env_override(monkeypatch):
    monkeypatch.setenv("ANALYSIS_QUOTA_MONTHLY", "5")
    assert QuotaConfig.from_env().monthly_limit == 5


def test_quota_status_exceeded_property():
    from app.quota import QuotaStatus

    now = datetime.now(timezone.utc)
    assert QuotaStatus(limit=5, used=5, resets_at=now).exceeded is True
    assert QuotaStatus(limit=5, used=4, resets_at=now).exceeded is False
    assert QuotaStatus(limit=5, used=6, resets_at=now).exceeded is True


# ── check_quota against real Postgres ──────────────────────────────────────


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


def _insert_job_and_cost(
    pg_owner_engine, *, user_id: uuid.UUID, created_at: datetime, call_type: str = "analyze"
) -> uuid.UUID:
    job_id, doc_id = uuid.uuid4(), uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, retry_count, created_at) "
                "VALUES (:id, :uid, :doc_id, 'abc', 'succeeded', 0, now())"
            ),
            {"id": job_id, "uid": user_id, "doc_id": doc_id},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_costs "
                "(id, job_id, user_id, call_type, model, input_tokens, output_tokens, created_at) "
                "VALUES (:id, :job_id, :uid, :call_type, 'claude-test', 10, 5, :created_at)"
            ),
            {
                "id": uuid.uuid4(),
                "job_id": job_id,
                "uid": user_id,
                "call_type": call_type,
                "created_at": created_at,
            },
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


def _app_session(pg_app_engine, user_id: uuid.UUID):
    conn = pg_app_engine.connect()
    conn.execute(text(f"SET LOCAL app.user_id = '{user_id}'"))
    return conn


def test_check_quota_counts_distinct_jobs_this_month(pg_owner_engine, pg_app_engine, cleanup_rows):
    user_id = uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    this_month = datetime(2026, 9, 10, tzinfo=timezone.utc)
    _insert_job_and_cost(pg_owner_engine, user_id=user_id, created_at=this_month)
    _insert_job_and_cost(pg_owner_engine, user_id=user_id, created_at=this_month)

    with _app_session(pg_app_engine, user_id) as conn:
        from sqlalchemy.orm import Session

        session = Session(bind=conn)
        status = check_quota(session, config=QuotaConfig(monthly_limit=200), now=now)
        conn.rollback()

    assert status.used == 2
    assert status.limit == 200
    assert status.resets_at == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_check_quota_ignores_prior_months(pg_owner_engine, pg_app_engine, cleanup_rows):
    user_id = uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    last_month = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _insert_job_and_cost(pg_owner_engine, user_id=user_id, created_at=last_month)

    with _app_session(pg_app_engine, user_id) as conn:
        from sqlalchemy.orm import Session

        session = Session(bind=conn)
        status = check_quota(session, config=QuotaConfig(monthly_limit=200), now=now)
        conn.rollback()

    assert status.used == 0


def test_check_quota_counts_distinct_job_not_row_count(pg_owner_engine, pg_app_engine, cleanup_rows):
    """A job with multiple provider calls (analyze + N entailment) writes
    multiple analysis_costs rows but must count once -- COUNT(DISTINCT
    job_id), not COUNT(*)."""
    user_id = uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    this_month = datetime(2026, 9, 10, tzinfo=timezone.utc)
    job_id, doc_id = uuid.uuid4(), uuid.uuid4()
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, retry_count, created_at) "
                "VALUES (:id, :uid, :doc_id, 'abc', 'succeeded', 0, now())"
            ),
            {"id": job_id, "uid": user_id, "doc_id": doc_id},
        )
        for call_type in ("analyze", "entailment", "entailment"):
            conn.execute(
                text(
                    "INSERT INTO analysis_costs "
                    "(id, job_id, user_id, call_type, model, input_tokens, output_tokens, created_at) "
                    "VALUES (:id, :job_id, :uid, :call_type, 'claude-test', 10, 5, :created_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "job_id": job_id,
                    "uid": user_id,
                    "call_type": call_type,
                    "created_at": this_month,
                },
            )
        conn.commit()

    with _app_session(pg_app_engine, user_id) as conn:
        from sqlalchemy.orm import Session

        session = Session(bind=conn)
        status = check_quota(session, config=QuotaConfig(monthly_limit=200), now=now)
        conn.rollback()

    assert status.used == 1


def test_check_quota_scoped_by_rls_not_other_users(pg_owner_engine, pg_app_engine, cleanup_rows):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    cleanup_rows.extend([user_a, user_b])
    _insert_user(pg_owner_engine, user_a)
    _insert_user(pg_owner_engine, user_b)
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    this_month = datetime(2026, 9, 10, tzinfo=timezone.utc)
    _insert_job_and_cost(pg_owner_engine, user_id=user_a, created_at=this_month)
    _insert_job_and_cost(pg_owner_engine, user_id=user_b, created_at=this_month)
    _insert_job_and_cost(pg_owner_engine, user_id=user_b, created_at=this_month)

    with _app_session(pg_app_engine, user_a) as conn:
        from sqlalchemy.orm import Session

        session = Session(bind=conn)
        status = check_quota(session, config=QuotaConfig(monthly_limit=200), now=now)
        conn.rollback()

    assert status.used == 1

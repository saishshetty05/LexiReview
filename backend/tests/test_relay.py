"""app/relay.py — the outbox sweeper (CONTRACTS.md §1/§5, v1.13).

Runs against real Postgres, connecting as the actual `relay` role
(`pg_relay_engine`, migration 011) exactly like the sweeper does in
production -- not a mock connection. `send_task` itself is monkeypatched
(these are relay/DB tests, not a Celery broker integration test), mirroring
test_upload_api.py's approach to the same call.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import app.worker as worker_module
from app.jobs import backoff_seconds
from app.relay import sweep_outbox


@pytest.fixture()
def recorded_send_task(monkeypatch):
    calls: list[tuple[str, list]] = []

    def fake_send_task(name, args=None, **kwargs):
        calls.append((name, args))

    monkeypatch.setattr(worker_module.celery_app, "send_task", fake_send_task)
    return calls


@pytest.fixture()
def seed_outbox_row(pg_owner_engine, pg_app_engine):
    """Seed a user + job (as owner) and an outbox row (as app_user, the real
    write path), with the actual {"task_name", "args"} payload shape
    app/main.py writes -- unlike test_migration_011.py's generic
    seeded_job_and_outbox fixture, which uses a placeholder payload not
    meaningful to send_task.
    """
    created_job_ids: list[uuid.UUID] = []
    created_user_ids: list[uuid.UUID] = []

    def _seed(*, attempts: int = 0, last_attempt_at: datetime | None = None) -> uuid.UUID:
        user_id = uuid.uuid4()
        doc_id = uuid.uuid4()
        job_id = uuid.uuid4()
        created_user_ids.append(user_id)
        created_job_ids.append(job_id)

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
                {"id": job_id, "uid": user_id, "doc_id": doc_id},
            )
            conn.commit()

        with pg_app_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO outbox "
                    "(id, job_id, payload_json, attempts, last_attempt_at, created_at) "
                    "VALUES (:id, :job_id, CAST(:payload AS jsonb), :attempts, :last_attempt_at, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "job_id": job_id,
                    "payload": json.dumps(
                        {"task_name": "analyze_document", "args": [str(doc_id), str(user_id)]}
                    ),
                    "attempts": attempts,
                    "last_attempt_at": last_attempt_at,
                },
            )
            conn.commit()
        return job_id

    yield _seed

    with pg_owner_engine.connect() as conn:
        for job_id in created_job_ids:
            conn.execute(text("DELETE FROM analysis_jobs WHERE id = :id"), {"id": job_id})
        for user_id in created_user_ids:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        conn.commit()


def _outbox_row(pg_owner_engine, job_id):
    with pg_owner_engine.connect() as conn:
        return conn.execute(
            text("SELECT attempts, last_attempt_at, delivered_at FROM outbox WHERE job_id = :id"),
            {"id": job_id},
        ).fetchone()


def _payload_args(pg_owner_engine, job_id) -> tuple[str, str]:
    with pg_owner_engine.connect() as conn:
        payload = conn.execute(
            text("SELECT payload_json FROM outbox WHERE job_id = :id"), {"id": job_id}
        ).scalar_one()
    return tuple(payload["args"])


def test_sweep_delivers_undelivered_row(
    pg_owner_engine, pg_relay_engine, seed_outbox_row, recorded_send_task
):
    job_id = seed_outbox_row()

    sweep_outbox(engine=pg_relay_engine)

    assert len(recorded_send_task) == 1
    assert recorded_send_task[0][0] == "analyze_document"

    row = _outbox_row(pg_owner_engine, job_id)
    assert row.attempts == 1
    assert row.last_attempt_at is not None
    assert row.delivered_at is not None


def test_sweep_failed_delivery_bumps_attempts_without_delivering(
    pg_owner_engine, pg_relay_engine, seed_outbox_row, monkeypatch
):
    def failing_send_task(*args, **kwargs):
        raise ConnectionError("simulated broker outage")

    monkeypatch.setattr(worker_module.celery_app, "send_task", failing_send_task)

    job_id = seed_outbox_row()
    sweep_outbox(engine=pg_relay_engine)

    row = _outbox_row(pg_owner_engine, job_id)
    assert row.attempts == 1
    assert row.last_attempt_at is not None
    assert row.delivered_at is None


def test_sweep_skips_row_within_backoff_window(
    pg_owner_engine, pg_relay_engine, seed_outbox_row, recorded_send_task
):
    # attempts=1 -> backoff_seconds(1) == 2s; a last_attempt_at 1s ago is not due yet.
    recent = datetime.now(timezone.utc) - timedelta(seconds=1)
    job_id = seed_outbox_row(attempts=1, last_attempt_at=recent)

    sweep_outbox(engine=pg_relay_engine)

    assert recorded_send_task == []
    row = _outbox_row(pg_owner_engine, job_id)
    assert row.attempts == 1
    assert row.delivered_at is None


def test_sweep_delivers_row_once_backoff_window_elapses(
    pg_owner_engine, pg_relay_engine, seed_outbox_row, recorded_send_task
):
    stale = datetime.now(timezone.utc) - timedelta(seconds=backoff_seconds(1) + 1)
    job_id = seed_outbox_row(attempts=1, last_attempt_at=stale)

    sweep_outbox(engine=pg_relay_engine)

    assert len(recorded_send_task) == 1
    row = _outbox_row(pg_owner_engine, job_id)
    assert row.attempts == 2
    assert row.delivered_at is not None


def test_sweep_skips_row_at_max_attempts(
    pg_owner_engine, pg_relay_engine, seed_outbox_row, recorded_send_task
):
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    job_id = seed_outbox_row(attempts=5, last_attempt_at=stale)

    sweep_outbox(engine=pg_relay_engine, max_attempts=5)

    assert recorded_send_task == []
    row = _outbox_row(pg_owner_engine, job_id)
    assert row.attempts == 5
    assert row.delivered_at is None


def test_sweep_dead_letters_row_exhausted_on_this_attempt(
    pg_owner_engine, pg_relay_engine, seed_outbox_row, monkeypatch
):
    """A row failing its LAST allowed attempt (attempts 4 -> 5, max=5) is
    dead-lettered immediately, using doc_id/user_id straight out of the
    row's own payload_json -- never a fresh analysis_jobs lookup, so this
    needs no grant beyond the relay role's existing SELECT+UPDATE on outbox.
    """
    def failing_send_task(*args, **kwargs):
        raise ConnectionError("simulated broker outage")

    monkeypatch.setattr(worker_module.celery_app, "send_task", failing_send_task)

    dead_letter_calls = []
    monkeypatch.setattr(
        worker_module.dead_letter_record,
        "apply_async",
        lambda kwargs, queue: dead_letter_calls.append((kwargs, queue)),
    )

    stale = datetime.now(timezone.utc) - timedelta(days=1)
    job_id = seed_outbox_row(attempts=4, last_attempt_at=stale)
    doc_id, user_id = _payload_args(pg_owner_engine, job_id)

    sweep_outbox(engine=pg_relay_engine, max_attempts=5)

    row = _outbox_row(pg_owner_engine, job_id)
    assert row.attempts == 5
    assert row.delivered_at is None

    assert len(dead_letter_calls) == 1
    kwargs, queue = dead_letter_calls[0]
    assert queue == "dead_letter"
    assert kwargs == {
        "job_id": str(job_id),
        "doc_id": doc_id,
        "user_id": user_id,
        "error_reason": "outbox_delivery_exhausted",
    }


def test_sweep_does_not_dead_letter_before_budget_exhausted(
    pg_owner_engine, pg_relay_engine, seed_outbox_row, monkeypatch
):
    """A failed attempt that still leaves attempts < max_attempts must not
    dead-letter -- only the transition onto the budget's last attempt does.
    """
    def failing_send_task(*args, **kwargs):
        raise ConnectionError("simulated broker outage")

    monkeypatch.setattr(worker_module.celery_app, "send_task", failing_send_task)

    dead_letter_calls = []
    monkeypatch.setattr(
        worker_module.dead_letter_record,
        "apply_async",
        lambda kwargs, queue: dead_letter_calls.append((kwargs, queue)),
    )

    stale = datetime.now(timezone.utc) - timedelta(days=1)
    seed_outbox_row(attempts=2, last_attempt_at=stale)

    sweep_outbox(engine=pg_relay_engine, max_attempts=5)

    assert dead_letter_calls == []

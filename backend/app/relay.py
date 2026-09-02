"""Outbox relay/sweeper — the delivery half of CONTRACTS.md §1/§5 (v1.13).

`upload_document` (app/main.py) writes the job row and its outbox row
atomically and makes no broker call itself. This module is the other side:
it sweeps undelivered outbox rows and hands them to the broker, retrying
with backoff up to a fixed attempt budget. Wired into Celery beat by
worker.py (a satellite process like the worker, so importing worker.py's
heavy LLM/analysis chain here is fine -- unlike in main.py's API process).

Runs as the dedicated `relay` Postgres role (migration 011): SELECT+UPDATE
on `outbox` only, no RLS, no access to any user table -- see
docs/MIGRATION_011_RLS_DECISION.md. This module must never import
app_user_session or attempt to touch a user-owned table.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

from app.db import _normalize
from app.jobs import backoff_seconds

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 5


def _resolve_relay_database_url() -> str:
    url = os.environ.get("RELAY_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "RELAY_DATABASE_URL is not set. The relay must connect as the "
            "least-privilege `relay` role (migration 011) -- see .env.example."
        )
    return url


# Built lazily, not at import time: importing this module (e.g. from a test
# that passes its own engine to sweep_outbox) must not require
# RELAY_DATABASE_URL to be set.
_relay_engine: Engine | None = None


def _default_relay_engine() -> Engine:
    global _relay_engine
    if _relay_engine is None:
        _relay_engine = create_engine(
            _normalize(_resolve_relay_database_url()), poolclass=QueuePool, pool_size=5, max_overflow=0
        )
    return _relay_engine


def _is_due(last_attempt_at: datetime | None, attempts: int) -> bool:
    if last_attempt_at is None:
        return True
    if last_attempt_at.tzinfo is None:
        last_attempt_at = last_attempt_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_attempt_at >= timedelta(seconds=backoff_seconds(attempts))


def sweep_outbox(*, engine: Engine | None = None, max_attempts: int = MAX_DELIVERY_ATTEMPTS) -> None:
    """One sweep pass: deliver every undelivered, due outbox row.

    FOR UPDATE SKIP LOCKED is cheap insurance against a future second relay
    instance double-sending -- only one is deployed today, but the query
    stays correct if that ever changes. Rows at max_attempts are left alone:
    the relay role has no grant on analysis_jobs, so it cannot resolve
    doc_id/user_id to route them through worker.py's dead_letter.record --
    forcing that would mean widening the relay's grants past outbox alone,
    undermining the least-privilege design locked in by migration 011. A
    stuck row is visible to an operator via:
        SELECT * FROM outbox WHERE delivered_at IS NULL AND attempts >= 5;

    `engine` defaults to the module's own RELAY_DATABASE_URL-backed engine;
    tests pass conftest.py's `pg_relay_engine` fixture instead.
    """
    # Imported lazily: app.worker pulls in anthropic/llm_client/analysis_pipeline,
    # which only the worker/relay processes should ever load, never the API.
    from app.worker import celery_app

    engine = engine or _default_relay_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, job_id, payload_json, attempts, last_attempt_at "
                "FROM outbox WHERE delivered_at IS NULL AND attempts < :max_attempts "
                "FOR UPDATE SKIP LOCKED"
            ),
            {"max_attempts": max_attempts},
        ).fetchall()

        for row in rows:
            if not _is_due(row.last_attempt_at, row.attempts):
                continue

            try:
                payload = row.payload_json
                celery_app.send_task(payload["task_name"], args=payload["args"])
            except Exception:
                logger.warning("outbox delivery attempt failed for job_id=%s", row.job_id, exc_info=True)
                conn.execute(
                    text(
                        "UPDATE outbox SET attempts = attempts + 1, last_attempt_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": row.id},
                )
                continue

            conn.execute(
                text(
                    "UPDATE outbox SET attempts = attempts + 1, last_attempt_at = now(), "
                    "delivered_at = now() WHERE id = :id"
                ),
                {"id": row.id},
            )

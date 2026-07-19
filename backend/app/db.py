"""SQLAlchemy engine/session setup, shared by the API and the worker."""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def _normalize(url: str) -> str:
    # requirements.txt pins psycopg3, but DATABASE_URL in .env.example uses the
    # driver-less "postgresql://" scheme, which SQLAlchemy resolves to psycopg2.
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _resolve_database_url() -> str:
    # The app MUST connect as app_user (non-superuser, RLS-enforced — see
    # migration 001 and the 2026-07-15 RLS spike finding), never as the
    # lexireview owner role. Falling back to DATABASE_URL is a temporary
    # bridge for environments that haven't run migration 001 yet
    # (docs/DECISION_LOG.md 2026-07-15) and must be treated as a misconfiguration.
    app_url = os.environ.get("APP_DATABASE_URL")
    if app_url:
        return app_url
    if os.environ.get("CI") == "true":
        # The dev-machine WARNING below is not loud enough for CI: a missing
        # APP_DATABASE_URL there means every RLS-dependent test silently runs
        # as the owner/BYPASSRLS role, which can make an RLS regression pass
        # CI green (see docs/DECISION_LOG.md, PR #14 CI-red root cause). Fail
        # hard instead of warning so this class of misconfiguration can never
        # be silent in CI again.
        raise RuntimeError(
            "APP_DATABASE_URL is not set in CI. Refusing to fall back to "
            "DATABASE_URL (owner/BYPASSRLS role) — this would silently run "
            "RLS-dependent tests without RLS enforcement. Set APP_DATABASE_URL "
            "in the CI workflow env."
        )
    logger.warning(
        "APP_DATABASE_URL is not set — falling back to DATABASE_URL. "
        "This means the app is running with OWNER credentials and RLS is NOT "
        "enforced. Set APP_DATABASE_URL (app_user role, created by migration "
        "001) before running against real data."
    )
    return os.environ.get(
        "DATABASE_URL", "postgresql://lexireview:lexireview_dev@postgres:5432/lexireview"
    )


DATABASE_URL = _normalize(_resolve_database_url())

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def app_user_session(user_id: uuid.UUID | None) -> Iterator[Session]:
    """Open a session and apply `SET LOCAL app.user_id` for RLS scoping.

    Shared by every code path (API, worker) that reads/writes user-owned
    rows, so RLS is the single source of truth for row scoping instead of
    each caller remembering to add its own user_id filter. Commits on a
    clean exit, rolls back on any exception.

    `user_id=None` skips the SET LOCAL entirely and leaves app.user_id
    unset for the session. This is ONLY safe against a table/policy that
    doesn't depend on app.user_id being set -- today that is exactly one
    case, `users` SELECT under the email_lookup policy (migration 003),
    used by login to find a row by email before any user_id is known. Every
    other RLS policy in the schema hard-errors on an unset app.user_id
    (see docs/DECISION_LOG.md, 2026-07-16) rather than silently scoping to
    nothing, so passing None anywhere else will fail loudly, not leak rows.
    """
    session = SessionLocal()
    try:
        if user_id is not None:
            # SET LOCAL, like CREATE ROLE, is DDL/config -- Postgres rejects
            # bind parameters there ("syntax error at or near $1").
            # uuid.UUID(...) both validates the input and produces a string
            # of only hex digits and hyphens, which is safe to inline
            # directly (same pattern as backend/tests/test_rls_smoke.py).
            session.execute(text(f"SET LOCAL app.user_id = '{uuid.UUID(str(user_id))}'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

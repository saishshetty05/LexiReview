"""SQLAlchemy engine/session setup, shared by the API and the worker."""
from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.models import Base


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
    engine.dispose()


# ── Real-Postgres fixtures (schema/RLS tests) ──────────────────────────────
# Require `alembic upgrade head` to have already been run against DATABASE_URL
# (CI does this in its own step; locally: docker compose up -d postgres, then
# `python -m alembic upgrade head` from backend/, same as test_migration_apply.py
# and test_rls_smoke.py's docstrings describe).


def _pg_conn_params() -> dict[str, str]:
    return {
        "user": os.environ.get("POSTGRES_USER", "lexireview"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lexireview_dev"),
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "db": os.environ.get("POSTGRES_DB", "lexireview"),
    }


@pytest.fixture(scope="session")
def pg_owner_engine():
    """Connects as the owner/superuser role (DATABASE_URL) — used for schema
    introspection and for cleanup that app_user isn't privileged to do.
    """
    p = _pg_conn_params()
    url = f"postgresql+psycopg://{p['user']}:{p['password']}@{p['host']}:{p['port']}/{p['db']}"
    engine = create_engine(url, poolclass=QueuePool, pool_size=5, max_overflow=0)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def pg_app_engine():
    """Connects as app_user (non-superuser, RLS-enforced) — mirrors how the
    real api/worker containers connect via APP_DATABASE_URL.
    """
    p = _pg_conn_params()
    app_password = os.environ["APP_USER_PASSWORD"]
    url = f"postgresql+psycopg://app_user:{app_password}@{p['host']}:{p['port']}/{p['db']}"
    engine = create_engine(url, poolclass=QueuePool, pool_size=5, max_overflow=0)
    yield engine
    engine.dispose()

from __future__ import annotations

import os
from urllib.parse import urlparse

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
    """Resolve the Postgres host/port/db these fixtures connect to.

    The "vantage point" bug: pytest's own network vantage point (host shell
    vs. inside the api container vs. a CI runner) determines what hostname
    actually reaches Postgres, and a single hardcoded default can't be right
    for all three. Resolution chain, in order:

    1. TEST_DATABASE_URL — set this to override the vantage point explicitly
       (rarely needed; covers any setup the two defaults below don't).
    2. DATABASE_URL — already set correctly for the two vantage points that
       matter day to day: `postgres` (the compose service name) from inside
       the api container, `localhost` in CI (ci.yml sets it directly). Reusing
       it means plain `docker compose exec api pytest -q` and CI both work
       with no extra env override.
    3. A bare-localhost default, for a host shell with no compose env sourced
       at all.

    Only host/port/db are taken from the resolved URL — user/password stay
    on their own POSTGRES_USER/POSTGRES_PASSWORD env vars (or app_user's
    APP_USER_PASSWORD, in pg_app_engine) since owner and app roles need
    different credentials than whatever DATABASE_URL's URL-embedded ones are.
    """
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = str(parsed.port or 5432)
        db = parsed.path.lstrip("/") or "lexireview"
    else:
        host, port, db = "localhost", "5432", "lexireview"
    return {
        "user": os.environ.get("POSTGRES_USER", "lexireview"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lexireview_dev"),
        "host": host,
        "port": port,
        "db": db,
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


@pytest.fixture(scope="session")
def pg_relay_engine():
    """Connects as the `relay` role (migration 011) — the cross-user outbox
    sweeper. Non-superuser, non-BYPASSRLS, granted SELECT+UPDATE on `outbox`
    ONLY. Mirrors how the future relay process connects via RELAY_DATABASE_URL.
    """
    p = _pg_conn_params()
    relay_password = os.environ["RELAY_USER_PASSWORD"]
    url = f"postgresql+psycopg://relay:{relay_password}@{p['host']}:{p['port']}/{p['db']}"
    engine = create_engine(url, poolclass=QueuePool, pool_size=5, max_overflow=0)
    yield engine
    engine.dispose()

r"""RLS spike (PRD SEC-2 / SETUP_GUIDE Phase 5).

Standalone script — not pytest. Run with:

    python backend/spikes/rls_spike.py

Proves that Postgres Row-Level Security, driven by `current_setting('app.user_id')`
and set per-request via `SET LOCAL`, actually isolates rows between users when the
app connects through a SQLAlchemy connection pool (mirrors how the app will run:
one shared pooled connection reused across different users' requests).

Setup/teardown run as `lexireview` (the .env-configured role), which owns the
table. But the dev-compose `lexireview` role turns out to be a Postgres
SUPERUSER with the BYPASSRLS attribute (`\du` in the container confirms this) —
and BYPASSRLS/superuser bypasses RLS unconditionally, even with FORCE ROW LEVEL
SECURITY set. So the two isolation assertions run through a second, dedicated
non-superuser role (`rls_spike_app`, created and dropped by this script) that
owns no BYPASSRLS/superuser attribute, mirroring what the real app role must be.

This script therefore proves two things, both required for RLS to hold in
production:
  1. FORCE ROW LEVEL SECURITY is required even for the table owner (ENABLE
     alone lets the owner see everything).
  2. The connecting role must NOT be superuser/BYPASSRLS — the app must use a
     dedicated low-privilege role, since `lexireview` (as currently configured
     in docker-compose/.env.example) cannot be trusted to enforce RLS at all.
"""

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

DB_USER = os.environ.get("POSTGRES_USER", "lexireview")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "lexireview_dev")
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "lexireview")

SUPERUSER_DATABASE_URL = (
    f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

TABLE = "rls_spike_docs"
APP_ROLE = "rls_spike_app"
APP_ROLE_PASSWORD = "rls_spike_app_pw"

APP_DATABASE_URL = (
    f"postgresql+psycopg://{APP_ROLE}:{APP_ROLE_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# Setup/teardown (DDL, role management) run as the superuser role from .env.
admin_engine = create_engine(SUPERUSER_DATABASE_URL, poolclass=QueuePool, pool_size=5, max_overflow=0)
# The two isolation assertions run as a dedicated non-superuser role — this is
# the pooled connection meant to mirror how the app itself will connect.
app_engine = create_engine(APP_DATABASE_URL, poolclass=QueuePool, pool_size=5, max_overflow=0)


def setup():
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(f"DROP ROLE IF EXISTS {APP_ROLE}"))
        conn.execute(
            text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PASSWORD}'")
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE {TABLE} (
                    id serial PRIMARY KEY,
                    user_id text NOT NULL,
                    content text NOT NULL
                )
                """
            )
        )
        conn.execute(text(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY"))
        # Table owner (lexireview) is a superuser with BYPASSRLS, so RLS would
        # be bypassed regardless of FORCE for that role specifically — but
        # FORCE is still required for any *other* non-BYPASSRLS role that
        # might own or be granted on the table in the future.
        conn.execute(text(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                f"""
                CREATE POLICY user_isolation ON {TABLE}
                USING (user_id = current_setting('app.user_id', true))
                """
            )
        )
        conn.execute(
            text(f"INSERT INTO {TABLE} (user_id, content) VALUES "
                 "('user-1', 'user1-doc-a'), ('user-1', 'user1-doc-b'), "
                 "('user-2', 'user2-doc-a'), ('user-2', 'user2-doc-b')")
        )
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {APP_ROLE}"))
        conn.execute(text(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO {APP_ROLE}"))
        conn.commit()


def teardown():
    app_engine.dispose()
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(f"DROP ROLE IF EXISTS {APP_ROLE}"))
        conn.commit()


def check_scoped_access() -> bool:
    """SET LOCAL app.user_id = 'user-1' should return only user-1's rows."""
    with app_engine.connect() as conn:
        conn.execute(text("SET LOCAL app.user_id = 'user-1'"))
        rows = conn.execute(text(f"SELECT user_id FROM {TABLE}")).fetchall()
        conn.rollback()

    user_ids = {row[0] for row in rows}
    ok = len(rows) == 2 and user_ids == {"user-1"}
    print(f"  rows returned: {len(rows)}, user_ids: {user_ids}")
    return ok


def check_unscoped_access() -> bool:
    """A pooled connection with no app.user_id set should see zero rows."""
    with app_engine.connect() as conn:
        rows = conn.execute(text(f"SELECT user_id FROM {TABLE}")).fetchall()
        conn.rollback()

    ok = len(rows) == 0
    print(f"  rows returned: {len(rows)}")
    return ok


def main() -> int:
    print(f"Connecting to {DB_HOST}:{DB_PORT}/{DB_NAME} as {DB_USER} (admin) "
          f"and {APP_ROLE} (app, non-superuser) ...")
    setup()
    print("Setup complete: table created, RLS enabled + forced, policy created, "
          f"4 rows inserted (2x user-1, 2x user-2), {APP_ROLE} role granted "
          "table privileges.\n")

    all_passed = True

    print("[1/2] SET LOCAL app.user_id = 'user-1' -> expect only user-1's rows")
    result_1 = check_scoped_access()
    print("[PASS]" if result_1 else "[FAIL]", "- scoped access returns only the current user's rows\n")
    all_passed &= result_1

    print("[2/2] No app.user_id set -> expect zero rows")
    result_2 = check_unscoped_access()
    print("[PASS]" if result_2 else "[FAIL]", "- unscoped access returns zero rows\n")
    all_passed &= result_2

    teardown()

    print("=" * 60)
    if all_passed:
        print("RESULT: PASS — RLS + FORCE RLS correctly isolates rows per "
              "current_setting('app.user_id') through a pooled connection.")
    else:
        print("RESULT: FAIL — RLS did not behave as expected. Do not proceed "
              "with this isolation strategy until fixed.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        teardown()
        raise

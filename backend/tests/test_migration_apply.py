"""Verifies migration 001 applied cleanly: all tables exist, app_user is a
real non-superuser/non-BYPASSRLS role, FORCE ROW LEVEL SECURITY is set on
every user-data table, and app_user's grants match the immutability design
in the migration docstring / docs/DECISION_LOG.md (2026-07-15) — in
particular, no DELETE anywhere, no UPDATE on the immutable tables.

Assumes `alembic upgrade head` has already been run against DATABASE_URL —
CI does this in its own step before pytest; locally, run it yourself first
(see backend/tests/conftest.py).
"""
from __future__ import annotations

from sqlalchemy import text

EXPECTED_TABLES = {"users", "documents", "analysis_jobs", "analysis_results", "audit_log"}

IMMUTABLE_TABLES = ("documents", "analysis_results", "audit_log")


def test_all_tables_exist(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).fetchall()
    tablenames = {r[0] for r in rows}
    assert EXPECTED_TABLES <= tablenames


def test_app_user_role_is_non_superuser_no_bypassrls(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'app_user'")
        ).fetchone()
    assert row is not None, "app_user role was not created by migration 001"
    rolsuper, rolbypassrls = row
    assert rolsuper is False, "app_user must not be superuser (RLS spike finding, 2026-07-15)"
    assert rolbypassrls is False, "app_user must not have BYPASSRLS (RLS spike finding, 2026-07-15)"


def test_force_rls_enabled_on_every_user_data_table(pg_owner_engine):
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(:names)"
            ),
            {"names": list(EXPECTED_TABLES)},
        ).fetchall()
    by_name = {r[0]: (r[1], r[2]) for r in rows}
    assert set(by_name) == EXPECTED_TABLES
    for table, (rls_enabled, rls_forced) in by_name.items():
        assert rls_enabled is True, f"{table}: ROW LEVEL SECURITY not enabled"
        assert rls_forced is True, f"{table}: FORCE ROW LEVEL SECURITY not set"


def test_app_user_has_no_delete_grant_anywhere(pg_owner_engine):
    """Account-deletion cascade (FR-15 / DPDP 24h SLA) deliberately does not
    run via app_user — see the migration 001 docstring and the 2026-07-15
    DECISION_LOG.md row. Enforced here by asserting DELETE is withheld.
    """
    privileges_by_table = _app_user_privileges(pg_owner_engine)
    for table in EXPECTED_TABLES:
        assert "DELETE" not in privileges_by_table.get(table, set()), (
            f"app_user must never be granted DELETE on {table}"
        )


def test_immutable_tables_are_insert_and_select_only(pg_owner_engine):
    privileges_by_table = _app_user_privileges(pg_owner_engine)
    for table in IMMUTABLE_TABLES:
        assert privileges_by_table[table] == {"SELECT", "INSERT"}, (
            f"{table}: expected INSERT+SELECT only for app_user, "
            f"got {privileges_by_table[table]}"
        )


def test_analysis_jobs_and_users_allow_update(pg_owner_engine):
    privileges_by_table = _app_user_privileges(pg_owner_engine)
    # analysis_jobs: the worker transitions job state in place (CONTRACTS.md §1).
    assert privileges_by_table["analysis_jobs"] == {"SELECT", "INSERT", "UPDATE"}
    # users: profile updates allowed.
    assert privileges_by_table["users"] == {"SELECT", "INSERT", "UPDATE"}


def _app_user_privileges(pg_owner_engine) -> dict[str, set[str]]:
    with pg_owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'app_user'"
            )
        ).fetchall()
    privileges_by_table: dict[str, set[str]] = {}
    for table_name, privilege_type in rows:
        privileges_by_table.setdefault(table_name, set()).add(privilege_type)
    return privileges_by_table

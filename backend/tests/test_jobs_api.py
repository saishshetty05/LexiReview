"""Tests for GET /jobs/{id} and GET /jobs/{id}/findings (CONTRACTS.md §3(a)).
Runs against real Postgres (RLS matters here -- these routes are the first
consumers of app_user_session outside the worker). Auth is the real
get_current_user dependency (app/main.py) -- a JWT cookie, minted via
app.auth directly rather than a login HTTP round-trip so these tests don't
touch the login rate limiter (app/auth.py, not modified by this PR).
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


def _register_user(pg_owner_engine) -> tuple[uuid.UUID, dict[str, str]]:
    """Creates a real user via app.auth.register and returns (user_id, an
    auth cookie dict ready to pass as `cookies=` to the test client).
    """
    email = f"jobs-api-test-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    token = create_access_token(user.id)
    return user.id, {ACCESS_TOKEN_COOKIE: token}


def _insert_job(
    pg_owner_engine,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    state: str = "queued",
    retry_count: int = 0,
    error_reason: str | None = None,
    started_at_now: bool = False,
    finished_at_now: bool = False,
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, retry_count, "
                " error_reason, created_at, started_at, finished_at) "
                "VALUES (:id, :user_id, :doc_id, 'hash', :state, :retry_count, "
                " :error_reason, now(), "
                + ("now()" if started_at_now else "NULL")
                + ", "
                + ("now()" if finished_at_now else "NULL")
                + ")"
            ),
            {
                "id": job_id,
                "user_id": user_id,
                "doc_id": doc_id,
                "state": state,
                "retry_count": retry_count,
                "error_reason": error_reason,
            },
        )
        conn.commit()


def _insert_finding(
    pg_owner_engine,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    category: str,
    severity: str,
    verification: str,
    confidence: str,
) -> None:
    payload = {
        "category": category,
        "severity": severity,
        "block_ids": [],
        "evidence_quote": "",
        "explanation": f"{category}/{severity}/{verification}",
        "verification": verification,
        "confidence": confidence,
    }
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_results "
                "(id, job_id, user_id, doc_id, doc_version_hash, category, severity, "
                " verification, confidence, payload, created_at) "
                "VALUES (:id, :job_id, :user_id, :doc_id, 'hash', :category, :severity, "
                " :verification, :confidence, :payload, now())"
            ),
            {
                "id": uuid.uuid4(),
                "job_id": job_id,
                "user_id": user_id,
                "doc_id": doc_id,
                "category": category,
                "severity": severity,
                "verification": verification,
                "confidence": confidence,
                "payload": json.dumps(payload),
            },
        )
        conn.commit()


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM analysis_results WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(
            text("DELETE FROM analysis_jobs WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def test_get_job_returns_polling_shape(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(
        pg_owner_engine,
        job_id=job_id,
        user_id=user_id,
        doc_id=doc_id,
        state="running",
        started_at_now=True,
    )

    resp = client.get(f"/jobs/{job_id}", cookies=auth_cookies)

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == str(job_id)
    assert body["state"] == "running"
    assert body["retry_count"] == 0
    assert body["started_at"] is not None
    assert body["finished_at"] is None
    assert body["error"] is None
    assert set(body.keys()) == {
        "job_id", "state", "retry_count", "created_at", "started_at", "finished_at", "error",
    }


def test_get_job_parses_error_reason_into_category_and_message(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(
        pg_owner_engine,
        job_id=job_id,
        user_id=user_id,
        doc_id=doc_id,
        state="failed",
        error_reason="unexpected_error: boom",
        finished_at_now=True,
    )

    resp = client.get(f"/jobs/{job_id}", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json()["error"] == {"category": "unexpected_error", "message": "boom"}


def test_get_job_404_for_nonexistent_job(pg_owner_engine, cleanup_rows):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    resp = client.get(f"/jobs/{uuid.uuid4()}", cookies=auth_cookies)
    assert resp.status_code == 404


def test_get_job_404_for_not_owned_job_same_as_nonexistent(pg_owner_engine, cleanup_rows):
    """Anti-enumeration: a real job_id owned by someone else must 404
    identically to one that doesn't exist at all (CONTRACTS.md §4 pattern)."""
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    owner_id, _owner_cookies = _register_user(pg_owner_engine)
    other_id, other_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(owner_id)
    cleanup_rows.append(other_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=owner_id, doc_id=doc_id)

    not_owned_resp = client.get(f"/jobs/{job_id}", cookies=other_cookies)
    nonexistent_resp = client.get(f"/jobs/{uuid.uuid4()}", cookies=other_cookies)

    assert not_owned_resp.status_code == nonexistent_resp.status_code == 404
    assert not_owned_resp.json() == nonexistent_resp.json()


def test_get_job_requires_auth_cookie(pg_owner_engine, cleanup_rows):
    """Confirms the jobs routes are actually wired to get_current_user (not
    just that the dependency itself works, which app/tests/test_auth.py
    already covers in isolation)."""
    missing_resp = client.get(f"/jobs/{uuid.uuid4()}")
    assert missing_resp.status_code == 401

    tampered_resp = client.get(
        f"/jobs/{uuid.uuid4()}", cookies={ACCESS_TOKEN_COOKIE: "not-a-real-token"}
    )
    assert tampered_resp.status_code == 401


def test_findings_returns_verified_first_then_unverified_sorted_by_severity(
    pg_owner_engine, cleanup_rows
):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id, state="succeeded")

    # Inserted deliberately out of order.
    _insert_finding(
        pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id,
        category="liability", severity="low", verification="unverified", confidence="needs_review",
    )
    _insert_finding(
        pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id,
        category="payment", severity="high", verification="verified", confidence="standard",
    )
    _insert_finding(
        pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id,
        category="termination", severity="high", verification="unverified", confidence="needs_review",
    )
    _insert_finding(
        pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id,
        category="indemnity", severity="medium", verification="verified", confidence="standard",
    )

    resp = client.get(f"/jobs/{job_id}/findings", cookies=auth_cookies)

    assert resp.status_code == 200
    categories = [f["category"] for f in resp.json()]
    # verified group (severity order: payment/high, indemnity/medium) then
    # unverified group (severity order: termination/high, liability/low).
    assert categories == ["payment", "indemnity", "termination", "liability"]


def test_findings_empty_list_for_job_with_no_results_yet(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id, state="queued")

    resp = client.get(f"/jobs/{job_id}/findings", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == []


def test_findings_404_for_not_owned_job(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    owner_id, _owner_cookies = _register_user(pg_owner_engine)
    other_id, other_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(owner_id)
    cleanup_rows.append(other_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=owner_id, doc_id=doc_id)

    resp = client.get(f"/jobs/{job_id}/findings", cookies=other_cookies)
    assert resp.status_code == 404

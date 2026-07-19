"""Tests for GET /jobs/{id} and GET /jobs/{id}/findings (CONTRACTS.md §3(a)).
Runs against real Postgres (RLS matters here -- these routes are the first
consumers of app_user_session outside the worker). get_current_user_id
(app/deps.py) is a placeholder reading X-User-Id directly, not real auth --
see its docstring.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

client = TestClient(app)


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
    user_id, doc_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_job(
        pg_owner_engine,
        job_id=job_id,
        user_id=user_id,
        doc_id=doc_id,
        state="running",
        started_at_now=True,
    )

    resp = client.get(f"/jobs/{job_id}", headers={"X-User-Id": str(user_id)})

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
    user_id, doc_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_job(
        pg_owner_engine,
        job_id=job_id,
        user_id=user_id,
        doc_id=doc_id,
        state="failed",
        error_reason="unexpected_error: boom",
        finished_at_now=True,
    )

    resp = client.get(f"/jobs/{job_id}", headers={"X-User-Id": str(user_id)})

    assert resp.status_code == 200
    assert resp.json()["error"] == {"category": "unexpected_error", "message": "boom"}


def test_get_job_404_for_nonexistent_job(pg_owner_engine, cleanup_rows):
    user_id = uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)

    resp = client.get(f"/jobs/{uuid.uuid4()}", headers={"X-User-Id": str(user_id)})
    assert resp.status_code == 404


def test_get_job_404_for_not_owned_job_same_as_nonexistent(pg_owner_engine, cleanup_rows):
    """Anti-enumeration: a real job_id owned by someone else must 404
    identically to one that doesn't exist at all (CONTRACTS.md §4 pattern)."""
    owner_id, other_id, doc_id, job_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    cleanup_rows.append(owner_id)
    cleanup_rows.append(other_id)
    _insert_user(pg_owner_engine, owner_id)
    _insert_user(pg_owner_engine, other_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=owner_id, doc_id=doc_id)

    not_owned_resp = client.get(f"/jobs/{job_id}", headers={"X-User-Id": str(other_id)})
    nonexistent_resp = client.get(f"/jobs/{uuid.uuid4()}", headers={"X-User-Id": str(other_id)})

    assert not_owned_resp.status_code == nonexistent_resp.status_code == 404
    assert not_owned_resp.json() == nonexistent_resp.json()


def test_get_job_requires_valid_user_id_header(pg_owner_engine, cleanup_rows):
    resp = client.get(f"/jobs/{uuid.uuid4()}", headers={"X-User-Id": "not-a-uuid"})
    assert resp.status_code == 401


def test_findings_returns_verified_first_then_unverified_sorted_by_severity(
    pg_owner_engine, cleanup_rows
):
    user_id, doc_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
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

    resp = client.get(f"/jobs/{job_id}/findings", headers={"X-User-Id": str(user_id)})

    assert resp.status_code == 200
    categories = [f["category"] for f in resp.json()]
    # verified group (severity order: payment/high, indemnity/medium) then
    # unverified group (severity order: termination/high, liability/low).
    assert categories == ["payment", "indemnity", "termination", "liability"]


def test_findings_empty_list_for_job_with_no_results_yet(pg_owner_engine, cleanup_rows):
    user_id, doc_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cleanup_rows.append(user_id)
    _insert_user(pg_owner_engine, user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id, state="queued")

    resp = client.get(f"/jobs/{job_id}/findings", headers={"X-User-Id": str(user_id)})

    assert resp.status_code == 200
    assert resp.json() == []


def test_findings_404_for_not_owned_job(pg_owner_engine, cleanup_rows):
    owner_id, other_id, doc_id, job_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    cleanup_rows.append(owner_id)
    cleanup_rows.append(other_id)
    _insert_user(pg_owner_engine, owner_id)
    _insert_user(pg_owner_engine, other_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=owner_id, doc_id=doc_id)

    resp = client.get(f"/jobs/{job_id}/findings", headers={"X-User-Id": str(other_id)})
    assert resp.status_code == 404

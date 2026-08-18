"""Tests for CONTRACTS.md §7 (v1.7): the `decisions` table, the `finding_id`/
`decision` fields on GET /jobs/{id}/findings, and PUT
/jobs/{job_id}/findings/{finding_id}/decision. Runs against real Postgres
(RLS matters here), same style as test_jobs_api.py.
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
    email = f"decisions-test-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    token = create_access_token(user.id)
    return user.id, {ACCESS_TOKEN_COOKIE: token}


def _insert_job(pg_owner_engine, *, job_id: uuid.UUID, user_id: uuid.UUID, doc_id: uuid.UUID) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_jobs "
                "(id, user_id, doc_id, doc_version_hash, state, retry_count, created_at) "
                "VALUES (:id, :user_id, :doc_id, 'hash', 'succeeded', 0, now())"
            ),
            {"id": job_id, "user_id": user_id, "doc_id": doc_id},
        )
        conn.commit()


def _insert_finding(pg_owner_engine, *, job_id: uuid.UUID, user_id: uuid.UUID, doc_id: uuid.UUID) -> uuid.UUID:
    finding_id = uuid.uuid4()
    payload = {
        "category": "payment",
        "severity": "high",
        "block_ids": ["BLOCK_1"],
        "evidence_quote": "quote",
        "explanation": "explanation",
        "verification": "verified",
        "confidence": "standard",
    }
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_results "
                "(id, job_id, user_id, doc_id, doc_version_hash, category, severity, "
                " verification, confidence, payload, created_at) "
                "VALUES (:id, :job_id, :user_id, :doc_id, 'hash', 'payment', 'high', "
                " 'verified', 'standard', :payload, now())"
            ),
            {
                "id": finding_id,
                "job_id": job_id,
                "user_id": user_id,
                "doc_id": doc_id,
                "payload": json.dumps(payload),
            },
        )
        conn.commit()
    return finding_id


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        conn.execute(text("DELETE FROM decisions WHERE user_id = ANY(:ids)"), {"ids": created_user_ids})
        conn.execute(
            text("DELETE FROM analysis_results WHERE user_id = ANY(:ids)"), {"ids": created_user_ids}
        )
        conn.execute(text("DELETE FROM analysis_jobs WHERE user_id = ANY(:ids)"), {"ids": created_user_ids})
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def test_findings_default_to_pending_with_a_stable_finding_id(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    resp = client.get(f"/jobs/{job_id}/findings", cookies=auth_cookies)

    assert resp.status_code == 200
    [finding] = resp.json()
    assert finding["finding_id"] == str(finding_id)
    assert finding["decision"] == "pending"


def test_put_decision_inserts_then_updates(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    accept_resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted"},
        cookies=auth_cookies,
    )
    assert accept_resp.status_code == 200
    assert accept_resp.json() == {
        "finding_id": str(finding_id),
        "decision": "accepted",
        "severity_override": None,
    }

    findings_resp = client.get(f"/jobs/{job_id}/findings", cookies=auth_cookies)
    assert findings_resp.json()[0]["decision"] == "accepted"

    # Changing your mind is an update, not a second row (unique constraint
    # on (user_id, finding_id) would 500 on a naive INSERT-only path).
    dismiss_resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "dismissed"},
        cookies=auth_cookies,
    )
    assert dismiss_resp.status_code == 200
    assert dismiss_resp.json()["decision"] == "dismissed"

    with pg_owner_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM decisions WHERE finding_id = :fid"), {"fid": finding_id}
        ).scalar_one()
    assert count == 1


def test_put_decision_rejects_invalid_value(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "maybe"},
        cookies=auth_cookies,
    )
    assert resp.status_code == 422


def test_put_decision_404_for_finding_not_owned_by_caller(pg_owner_engine, cleanup_rows):
    """Anti-enumeration: a real finding owned by someone else 404s the same
    as a nonexistent one -- RLS makes session.get return None either way."""
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    owner_id, _owner_cookies = _register_user(pg_owner_engine)
    other_id, other_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(owner_id)
    cleanup_rows.append(other_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=owner_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=owner_id, doc_id=doc_id)

    not_owned_resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted"},
        cookies=other_cookies,
    )
    nonexistent_resp = client.put(
        f"/jobs/{job_id}/findings/{uuid.uuid4()}/decision",
        json={"decision": "accepted"},
        cookies=other_cookies,
    )

    assert not_owned_resp.status_code == nonexistent_resp.status_code == 404
    assert not_owned_resp.json() == nonexistent_resp.json()


def test_put_decision_404_when_finding_belongs_to_a_different_job(pg_owner_engine, cleanup_rows):
    """finding_id is real and owned by the caller, but not under this
    job_id -- must still 404, not silently accept the cross-job write."""
    doc_id = uuid.uuid4()
    job_id, other_job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    _insert_job(pg_owner_engine, job_id=other_job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=other_job_id, user_id=user_id, doc_id=doc_id)

    resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted"},
        cookies=auth_cookies,
    )
    assert resp.status_code == 404


def test_put_decision_requires_auth_cookie(pg_owner_engine, cleanup_rows):
    resp = client.put(
        f"/jobs/{uuid.uuid4()}/findings/{uuid.uuid4()}/decision", json={"decision": "accepted"}
    )
    assert resp.status_code == 401


# ── severity_override (CONTRACTS.md §7a, v1.8) ──────────────────────────


def test_put_decision_sets_severity_override(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted", "severity_override": "medium"},
        cookies=auth_cookies,
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "finding_id": str(finding_id),
        "decision": "accepted",
        "severity_override": "medium",
    }


def test_put_decision_omitting_severity_override_leaves_it_untouched(pg_owner_engine, cleanup_rows):
    """Absent key != explicit null: a later accept/dismiss-only call must not
    silently clear a previously-set override."""
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted", "severity_override": "low"},
        cookies=auth_cookies,
    )
    resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "dismissed"},
        cookies=auth_cookies,
    )

    assert resp.status_code == 200
    assert resp.json()["severity_override"] == "low"


def test_put_decision_explicit_null_clears_severity_override(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted", "severity_override": "low"},
        cookies=auth_cookies,
    )
    resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted", "severity_override": None},
        cookies=auth_cookies,
    )

    assert resp.status_code == 200
    assert resp.json()["severity_override"] is None


def test_put_decision_rejects_invalid_severity_override(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    resp = client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted", "severity_override": "critical"},
        cookies=auth_cookies,
    )
    assert resp.status_code == 422


def test_get_job_findings_exposes_severity_override(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    finding_id = _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    client.put(
        f"/jobs/{job_id}/findings/{finding_id}/decision",
        json={"decision": "accepted", "severity_override": "medium"},
        cookies=auth_cookies,
    )
    resp = client.get(f"/jobs/{job_id}/findings", cookies=auth_cookies)

    assert resp.status_code == 200
    [finding] = resp.json()
    assert finding["severity_override"] == "medium"


def test_get_job_findings_severity_override_null_when_unset(pg_owner_engine, cleanup_rows):
    doc_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    _insert_job(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)
    _insert_finding(pg_owner_engine, job_id=job_id, user_id=user_id, doc_id=doc_id)

    resp = client.get(f"/jobs/{job_id}/findings", cookies=auth_cookies)

    assert resp.status_code == 200
    [finding] = resp.json()
    assert finding["severity_override"] is None

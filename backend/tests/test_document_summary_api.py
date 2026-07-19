"""Tests for GET /documents/{id}/summary (CONTRACTS.md §2b/§2c).
Runs against real Postgres (RLS matters here, same pattern as
test_jobs_api.py). Auth is the real get_current_user dependency; JWT cookie
minted via app.auth directly (no login HTTP round-trip, avoids the login
rate limiter).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_access_token, register
from app.main import ACCESS_TOKEN_COOKIE, app

client = TestClient(app)


def _register_user(pg_owner_engine) -> tuple[uuid.UUID, dict[str, str]]:
    email = f"doc-summary-test-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    token = create_access_token(user.id)
    return user.id, {ACCESS_TOKEN_COOKIE: token}


def _insert_document_summary(
    pg_owner_engine,
    *,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    doc_version_hash: str,
    model_version: str,
    payload: dict,
    created_at: datetime,
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO document_summaries "
                "(id, user_id, doc_id, doc_version_hash, model_version, payload, created_at) "
                "VALUES (:id, :uid, :doc_id, :hash, :model_version, :payload, :created_at)"
            ),
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "doc_id": doc_id,
                "hash": doc_version_hash,
                "model_version": model_version,
                "payload": json.dumps(payload),
                "created_at": created_at,
            },
        )
        conn.commit()


@pytest.fixture()
def cleanup_rows(pg_owner_engine):
    created_user_ids: list[uuid.UUID] = []
    yield created_user_ids
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM document_summaries WHERE user_id = ANY(:ids)"),
            {"ids": created_user_ids},
        )
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": created_user_ids})
        conn.commit()


def _sample_payload(marker: str) -> dict:
    return {
        "overview": f"Overview {marker}.",
        "key_terms": [{"label": "notice_period", "detail": "30 days written notice required"}],
        "risk_snapshot": {"high": 0, "medium": 1, "low": 0, "info": 0},
    }


def test_returns_summary_payload_shape_for_owned_document(pg_owner_engine, cleanup_rows):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    payload = _sample_payload("only")
    _insert_document_summary(
        pg_owner_engine,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash="hash1",
        model_version="model-v1",
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == payload
    assert set(resp.json().keys()) == {"overview", "key_terms", "risk_snapshot"}


def test_single_row_present_is_returned_without_ordering_issues(pg_owner_engine, cleanup_rows):
    """Edge case for the created_at DESC ordering: with nothing to order
    among, the one existing row must still come back, not None."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    payload = _sample_payload("single")
    _insert_document_summary(
        pg_owner_engine,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash="hash1",
        model_version="model-v1",
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == payload


def test_returns_most_recent_summary_when_multiple_exist(pg_owner_engine, cleanup_rows):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    older_payload = _sample_payload("older")
    newer_payload = _sample_payload("newer")

    # Inserted deliberately out of order to prove the endpoint sorts by
    # created_at, not insertion order.
    _insert_document_summary(
        pg_owner_engine,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash="hash-newer",
        model_version="model-v2",
        payload=newer_payload,
        created_at=now,
    )
    _insert_document_summary(
        pg_owner_engine,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash="hash-older",
        model_version="model-v1",
        payload=older_payload,
        created_at=now - timedelta(days=1),
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == newer_payload


def test_404_for_nonexistent_document(pg_owner_engine, cleanup_rows):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)

    resp = client.get(f"/documents/{uuid.uuid4()}/summary", cookies=auth_cookies)
    assert resp.status_code == 404


def test_404_for_not_owned_document_identical_to_nonexistent(pg_owner_engine, cleanup_rows):
    """Anti-enumeration: a real doc_id owned by someone else must produce a
    byte-identical response to one that doesn't exist at all -- same
    discipline as storage.fetch_document (CONTRACTS.md §4). Compares the
    full response (status, body, and headers minus request-specific ones),
    not just the status code, so a leaked detail in either would be caught.
    """
    owner_id, _owner_cookies = _register_user(pg_owner_engine)
    other_id, other_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(owner_id)
    cleanup_rows.append(other_id)
    doc_id = uuid.uuid4()
    _insert_document_summary(
        pg_owner_engine,
        user_id=owner_id,
        doc_id=doc_id,
        doc_version_hash="hash1",
        model_version="model-v1",
        payload=_sample_payload("owned"),
        created_at=datetime.now(timezone.utc),
    )

    not_owned_resp = client.get(f"/documents/{doc_id}/summary", cookies=other_cookies)
    nonexistent_resp = client.get(f"/documents/{uuid.uuid4()}/summary", cookies=other_cookies)

    assert not_owned_resp.status_code == nonexistent_resp.status_code == 404
    assert not_owned_resp.json() == nonexistent_resp.json()

    # Headers minus request-specific/non-deterministic ones (date, any
    # per-request correlation id) must match too -- a difference there
    # would be as much of a leak as a differing body.
    _volatile_headers = {"date", "content-length"}
    not_owned_headers = {
        k.lower(): v for k, v in not_owned_resp.headers.items() if k.lower() not in _volatile_headers
    }
    nonexistent_headers = {
        k.lower(): v
        for k, v in nonexistent_resp.headers.items()
        if k.lower() not in _volatile_headers
    }
    assert not_owned_headers == nonexistent_headers

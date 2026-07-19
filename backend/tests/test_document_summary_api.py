"""Tests for GET /documents/{id}/summary (CONTRACTS.md §2b/§2c).
Runs against real Postgres (RLS matters here, same pattern as
test_jobs_api.py). Auth is the real get_current_user dependency; JWT cookie
minted via app.auth directly (no login HTTP round-trip, avoids the login
rate limiter).

CONTRACTS.md §2b: "the document's current version and the pinned
model_version" -- the endpoint resolves documents.doc_id's highest `version`
first, then filters document_summaries to that version's doc_version_hash
(and to LLM_MODEL if set). An earlier revision ordered document_summaries
across ALL versions of a doc_id by created_at, which could silently serve a
STALE summary after a re-upload that hasn't been re-analyzed yet (caught in
PR #33 review by NikhilKatti29). test_404_for_current_version_when_only_older_version_has_summary
is the direct regression test for that bug.
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

_PINNED_MODEL = "test-pinned-model-v1"


@pytest.fixture(autouse=True)
def _pin_llm_model(monkeypatch):
    # Deterministic regardless of the ambient container env -- tests that
    # want the "LLM_MODEL unset" fallback path override this explicitly.
    monkeypatch.setenv("LLM_MODEL", _PINNED_MODEL)


def _register_user(pg_owner_engine) -> tuple[uuid.UUID, dict[str, str]]:
    email = f"doc-summary-test-{uuid.uuid4()}@example.invalid"
    user = register(email, "TestPass123!")
    token = create_access_token(user.id)
    return user.id, {ACCESS_TOKEN_COOKIE: token}


def _insert_document(
    pg_owner_engine,
    *,
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    version: int,
    doc_version_hash: str,
) -> None:
    with pg_owner_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, doc_id, user_id, version, doc_version_hash, original_filename, "
                "file_type, size_bytes, is_synthetic, created_at) "
                "VALUES (:id, :doc_id, :uid, :version, :hash, 'f.pdf', 'pdf', 10, true, now())"
            ),
            {
                "id": uuid.uuid4(),
                "doc_id": doc_id,
                "uid": user_id,
                "version": version,
                "hash": doc_version_hash,
            },
        )
        conn.commit()


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
        conn.execute(
            text("DELETE FROM documents WHERE user_id = ANY(:ids)"), {"ids": created_user_ids}
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
    _insert_document(pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash1")
    _insert_document_summary(
        pg_owner_engine,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash="hash1",
        model_version=_PINNED_MODEL,
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
    _insert_document(pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash1")
    _insert_document_summary(
        pg_owner_engine,
        user_id=user_id,
        doc_id=doc_id,
        doc_version_hash="hash1",
        model_version=_PINNED_MODEL,
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == payload


def test_returns_most_recent_by_created_at_when_llm_model_unset(
    pg_owner_engine, cleanup_rows, monkeypatch
):
    """Fallback path: with no LLM_MODEL pin, multiple model_versions can
    match the same current-version hash simultaneously -- most recent by
    created_at wins."""
    monkeypatch.delenv("LLM_MODEL", raising=False)
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    older_payload = _sample_payload("older-model")
    newer_payload = _sample_payload("newer-model")
    _insert_document(pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash1")

    # Inserted deliberately out of order to prove it sorts by created_at,
    # not insertion order.
    _insert_document_summary(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash1",
        model_version="model-v2", payload=newer_payload, created_at=now,
    )
    _insert_document_summary(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash1",
        model_version="model-v1", payload=older_payload, created_at=now - timedelta(hours=1),
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == newer_payload


def test_pinned_model_version_overrides_recency(pg_owner_engine, cleanup_rows):
    """Regression for the fix's model-pin filter: a NEWER row for a
    different, non-pinned model_version must NOT win over an OLDER row that
    matches LLM_MODEL -- the pin is a hard filter, not a tiebreak."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    pinned_payload = _sample_payload("pinned-but-older")
    other_payload = _sample_payload("newer-but-unpinned")
    _insert_document(pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash1")

    _insert_document_summary(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash1",
        model_version=_PINNED_MODEL, payload=pinned_payload, created_at=now - timedelta(hours=1),
    )
    _insert_document_summary(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash1",
        model_version="some-other-model", payload=other_payload, created_at=now,
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == pinned_payload


def test_404_for_current_version_when_only_older_version_has_summary(pg_owner_engine, cleanup_rows):
    """The exact bug caught in PR #33 review: a document re-uploaded (new
    version, new doc_version_hash) before re-analysis must 404 -- NOT
    silently serve the older version's summary as if it were current."""
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash-v1"
    )
    _insert_document_summary(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash-v1",
        model_version=_PINNED_MODEL, payload=_sample_payload("stale-v1"),
        created_at=datetime.now(timezone.utc),
    )
    # Re-upload: new version, new hash, not yet analyzed -- no summary row
    # for hash-v2 exists.
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=2, doc_version_hash="hash-v2"
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 404


def test_returns_current_version_summary_when_older_version_also_has_summary(
    pg_owner_engine, cleanup_rows
):
    user_id, auth_cookies = _register_user(pg_owner_engine)
    cleanup_rows.append(user_id)
    doc_id = uuid.uuid4()
    older_payload = _sample_payload("old-version")
    current_payload = _sample_payload("current-version")
    now = datetime.now(timezone.utc)

    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=1, doc_version_hash="hash-v1"
    )
    _insert_document_summary(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash-v1",
        model_version=_PINNED_MODEL, payload=older_payload, created_at=now - timedelta(days=1),
    )
    _insert_document(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, version=2, doc_version_hash="hash-v2"
    )
    _insert_document_summary(
        pg_owner_engine, user_id=user_id, doc_id=doc_id, doc_version_hash="hash-v2",
        model_version=_PINNED_MODEL, payload=current_payload, created_at=now,
    )

    resp = client.get(f"/documents/{doc_id}/summary", cookies=auth_cookies)

    assert resp.status_code == 200
    assert resp.json() == current_payload


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
    _insert_document(
        pg_owner_engine, user_id=owner_id, doc_id=doc_id, version=1, doc_version_hash="hash1"
    )
    _insert_document_summary(
        pg_owner_engine, user_id=owner_id, doc_id=doc_id, doc_version_hash="hash1",
        model_version=_PINNED_MODEL, payload=_sample_payload("owned"),
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

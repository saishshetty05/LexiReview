"""Reviewer decisions per CONTRACTS.md §7 (v1.7), PRD FR-13. One row per
(user_id, finding_id) -- upsert, not append-only, since a reviewer can
change their mind (unlike AnalysisResult, which is immutable).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Decision


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_decisions_for_findings(
    session: Session, *, user_id: uuid.UUID, finding_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Maps finding_id -> decision for every finding_id that has one recorded
    for this user. A finding with no row is "pending" -- that default is
    applied by the caller (get_job_findings), not here; this only returns
    what's actually stored.
    """
    if not finding_ids:
        return {}
    stmt = select(Decision).where(Decision.user_id == user_id, Decision.finding_id.in_(finding_ids))
    return {d.finding_id: d.decision for d in session.execute(stmt).scalars().all()}


def upsert_decision(
    session: Session, *, user_id: uuid.UUID, finding_id: uuid.UUID, decision: str
) -> Decision:
    """Insert or update the caller's decision for one finding. The caller
    (main.py's route) must already have verified finding_id belongs to a
    job the caller owns -- this function trusts user_id/finding_id as given.
    """
    stmt = select(Decision).where(Decision.user_id == user_id, Decision.finding_id == finding_id)
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        row = Decision(user_id=user_id, finding_id=finding_id, decision=decision)
        session.add(row)
    else:
        row.decision = decision
        row.updated_at = _now()
    session.flush()
    return row

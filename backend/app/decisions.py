"""Reviewer decisions per CONTRACTS.md §7 (v1.7), PRD FR-13. One row per
(user_id, finding_id) -- upsert, not append-only, since a reviewer can
change their mind (unlike AnalysisResult, which is immutable).

§7a (v1.8) adds severity_override and the severity-personalization example
query -- see docs/DECISION_LOG.md 2026-08-17 for the prompt-vs-embeddings
rationale.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisResult, Decision


class _Unset:
    """Sentinel distinguishing "severity_override not sent" from "sent as
    null" -- upsert_decision must tell the two apart (CONTRACTS.md §7a):
    omitted leaves an existing override untouched, explicit None clears it.
    """


UNSET = _Unset()

SEVERITY_EXAMPLES_LIMIT = 12


class DecisionInfo(NamedTuple):
    decision: str
    severity_override: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_decisions_for_findings(
    session: Session, *, user_id: uuid.UUID, finding_ids: list[uuid.UUID]
) -> dict[uuid.UUID, DecisionInfo]:
    """Maps finding_id -> (decision, severity_override) for every finding_id
    that has a row recorded for this user. A finding with no row is
    "pending"/no-override -- that default is applied by the caller
    (get_job_findings), not here; this only returns what's actually stored.
    """
    if not finding_ids:
        return {}
    stmt = select(Decision).where(Decision.user_id == user_id, Decision.finding_id.in_(finding_ids))
    return {
        d.finding_id: DecisionInfo(d.decision, d.severity_override)
        for d in session.execute(stmt).scalars().all()
    }


def upsert_decision(
    session: Session,
    *,
    user_id: uuid.UUID,
    finding_id: uuid.UUID,
    decision: str,
    severity_override: str | None | _Unset = UNSET,
) -> Decision:
    """Insert or update the caller's decision for one finding. The caller
    (main.py's route) must already have verified finding_id belongs to a
    job the caller owns -- this function trusts user_id/finding_id as given.

    severity_override defaults to UNSET (not None) so a plain accept/dismiss
    call never clobbers a previously-set override -- see CONTRACTS.md §7a.
    """
    stmt = select(Decision).where(Decision.user_id == user_id, Decision.finding_id == finding_id)
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        row = Decision(
            user_id=user_id,
            finding_id=finding_id,
            decision=decision,
            severity_override=None if severity_override is UNSET else severity_override,
        )
        session.add(row)
    else:
        row.decision = decision
        if severity_override is not UNSET:
            row.severity_override = severity_override
        row.updated_at = _now()
    session.flush()
    return row


def get_severity_examples(
    session: Session, *, user_id: uuid.UUID, limit: int = SEVERITY_EXAMPLES_LIMIT
) -> list[dict]:
    """CONTRACTS.md §7a: this user's most recent severity overrides, joined
    against the finding they were recorded on, formatted for use as
    few-shot examples in the next LLMClient.analyze() call. `evidence_quote`
    here is already the pseudonymised text the LLM itself returned (CLAUDE.md
    rule 3) -- safe to replay into a future prompt for the same user.
    Fixed cap, most-recent-first, not per-category (CONTRACTS.md §7a).
    """
    stmt = (
        select(
            AnalysisResult.category,
            AnalysisResult.severity,
            AnalysisResult.payload,
            Decision.severity_override,
        )
        .select_from(Decision)
        .join(AnalysisResult, AnalysisResult.id == Decision.finding_id)
        .where(Decision.user_id == user_id, Decision.severity_override.is_not(None))
        .order_by(Decision.updated_at.desc())
        .limit(limit)
    )
    return [
        {
            "category": category,
            "evidence_quote": payload.get("evidence_quote", ""),
            "original_severity": severity,
            "severity_override": severity_override,
        }
        for category, severity, payload, severity_override in session.execute(stmt).all()
    ]

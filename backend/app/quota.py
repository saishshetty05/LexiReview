"""quota.py — CONTRACTS.md §11 (v1.17), PRD FR-16 per-user monthly analysis
quota.

Checked in upload_document (app/main.py) after preflight passes, before any
of the documents/analysis_jobs/outbox rows are written -- same "rejection
writes nothing" property preflight itself established (CLAUDE.md rule 8).
Uses the caller's own RLS-scoped session (no explicit user_id filter,
matching the FR-6 dedup SELECT precedent in upload_document) -- the count
only ever sees the current session's own rows.

Month boundaries are computed in Python, not left to `date_trunc('month',
now())` in SQL: `now` is an explicit, injectable parameter so the December
-> January rollover and month-start/month-end edges are plain function
calls to test, not something that needs clock manipulation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnalysisCost

DEFAULT_ANALYSIS_QUOTA_MONTHLY = 200


@dataclass(frozen=True)
class QuotaConfig:
    monthly_limit: int

    @classmethod
    def from_env(cls) -> "QuotaConfig":
        raw = os.environ.get("ANALYSIS_QUOTA_MONTHLY", "").strip()
        limit = int(raw) if raw else DEFAULT_ANALYSIS_QUOTA_MONTHLY
        return cls(monthly_limit=limit)


@dataclass(frozen=True)
class QuotaStatus:
    limit: int
    used: int
    resets_at: datetime

    @property
    def exceeded(self) -> bool:
        return self.used >= self.limit


def month_start_utc(now: datetime) -> datetime:
    """The start of `now`'s UTC calendar month, at 00:00:00."""
    return now.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def next_month_start_utc(now: datetime) -> datetime:
    """The first moment of the UTC calendar month AFTER `now`'s --
    CONTRACTS.md §11's `resets_at`. Explicit December -> January handling:
    `month + 1` would overflow to a nonexistent month=13 otherwise.
    """
    start = month_start_utc(now)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def check_quota(
    session: Session,
    *,
    config: QuotaConfig | None = None,
    now: datetime | None = None,
) -> QuotaStatus:
    """CONTRACTS.md §11: `used` is `COUNT(DISTINCT job_id)` over the
    caller's analysis_costs rows created since the start of the current UTC
    calendar month -- a job counts iff it made at least one real LLM
    provider call this month, regardless of what happened to the job
    afterward (a dead-lettered or pre-LLM-failure job writes zero cost rows
    and doesn't count; a job that called the LLM counts once even if it
    later fails). RLS on analysis_costs scopes this to the caller
    automatically, same as the dedup SELECT already in upload_document.
    """
    config = config or QuotaConfig.from_env()
    now = now or datetime.now(timezone.utc)
    start = month_start_utc(now)

    used = session.execute(
        select(func.count(func.distinct(AnalysisCost.job_id))).where(
            AnalysisCost.created_at >= start,
        )
    ).scalar_one()

    return QuotaStatus(limit=config.monthly_limit, used=used, resets_at=next_month_start_utc(now))

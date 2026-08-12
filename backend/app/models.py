"""ORM models. analysis_jobs is CONTRACTS.md §1 (Person B's lane, unchanged here).
users / documents / analysis_results / audit_log are Person A's lane — schema
per CLAUDE.md and CONTRACTS.md §2. Migrations live in backend/alembic/versions/.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Migration 008: account-metadata-only admin role. is_admin is never
    # self-serve settable (no endpoint grants it) -- see docs/RUNBOOK.md for
    # the manual-DB-UPDATE bootstrap. active gates login (auth.authenticate)
    # and every subsequent request (main.get_current_user), not just login.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Document(Base):
    """Immutable version rows (CLAUDE.md rule 9): a re-upload inserts a new
    (doc_id, version) row, never an overwrite. `original_filename` is stored
    as sanitized-at-upload text only — the upload endpoint owns sanitization,
    this column does not accept raw paths.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("doc_id", "version", name="uq_documents_doc_id_version"),
        UniqueConstraint("user_id", "doc_version_hash", name="uq_documents_user_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    doc_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_version_hash: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # CONTRACTS.md §1a (v1.3): fail-closed default FALSE — untagged documents
    # are treated as real, so free-tier SYNTHETIC_ONLY gates refuse them.
    # Test fixtures must set this TRUE explicitly.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DocumentSummary(Base):
    """One row per (doc_version_hash, model_version), per CONTRACTS.md §2b.
    Written once by the worker after analysis completes; never updated
    afterward (app_user is granted INSERT/SELECT only — see migration 002).
    """

    __tablename__ = "document_summaries"
    __table_args__ = (
        UniqueConstraint(
            "doc_version_hash", "model_version", name="uq_document_summaries_hash_model"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    doc_version_hash: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AnalysisResult(Base):
    """One row per finding, per CONTRACTS.md §2 Storage. Findings are immutable
    once written (app_user is granted INSERT/SELECT only — see migration 001).
    """

    __tablename__ = "analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_jobs.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    doc_version_hash: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    verification: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, nullable=False)
    # JSONB in Postgres (production); falls back to generic JSON elsewhere so
    # Person B's SQLite-backed test fixture (tests/conftest.py) keeps working.
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Decision(Base):
    """One row per (user_id, finding_id), per CONTRACTS.md §7 (v1.7). Unlike
    AnalysisResult, this table is MUTABLE — a reviewer can change their mind
    — so app_user is granted UPDATE too (migration 006), same pattern as
    AnalysisJob/User rather than the immutable-artifact tables.
    """

    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("user_id", "finding_id", name="uq_decisions_user_finding"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_results.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    """Content-free audit trail (CLAUDE.md rule 2): metadata only, never
    document text, quotes, findings, or filenames. `ip` is purged after 90
    days by a future scheduled job — not implemented in this migration.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Migration 008: set only on admin_user_* events, the target account an
    # admin acted on. No FK, same as doc_id above -- must survive the
    # target row being deleted (admin_user_deleted logs the action, then
    # delete_account_cascade removes the row target_user_id points at).
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # INET in Postgres; falls back to a plain string elsewhere for the same
    # SQLite test-portability reason as `payload` above.
    ip: Mapped[str | None] = mapped_column(String().with_variant(INET(), "postgresql"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class JobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATES = {JobState.SUCCEEDED.value, JobState.FAILED.value}


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    doc_version_hash: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default=JobState.QUEUED.value)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CONTRACTS.md §2c (v1.4): same "category: message" format as
    # error_reason. NULL covers both "summary succeeded" and "summary
    # generation wasn't attempted" -- a single best-effort attempt, not a
    # first-class tracked entity, per the v1.4 worker-trigger design.
    summary_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

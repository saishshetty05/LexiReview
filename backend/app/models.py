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
    # MFA (TOTP) — opt-in, never mandatory. mfa_secret is the base32-encoded
    # TOTP secret (nullable: MFA not set up). mfa_enabled is the gate — only
    # when TRUE does login require a TOTP challenge.
    mfa_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Admin role (PR #1 of 3, paused 2026-08-12, resumed 2026-09-02):
    # is_admin flags admin access; active gates account suspension.
    # No JWT claim — require_admin re-checks the DB every request so
    # demotion/suspension takes effect immediately (PR #2).
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
    # CONTRACTS.md §7a (v1.8): NULL means "no override," not "override to
    # nothing." Unconstrained at the DB layer, same pattern as decision/
    # category/severity elsewhere -- validated at the API layer instead.
    severity_override: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RefreshToken(Base):
    """Rotating refresh tokens per CONTRACTS.md §9 (v1.11). Only the SHA-256
    hash of the raw token is ever stored (app/auth.py's create_refresh_token)
    -- unlike mfa_secret, a refresh token is only ever compared, never read
    back, so there is no reason to keep it reversible. Mutable via
    revoked_at (rotation/logout/reuse-detection all set it, never DELETE it
    -- app_user has no DELETE grant, migration 010), same
    mutable-but-not-deletable pattern as `decisions`.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # Admin actions (PR #1 of 3, resumed 2026-09-02): the user an admin
    # action targets. Nullable, no FK — matches doc_id precedent; the target
    # user may be deleted later but the audit row should survive.
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


class Outbox(Base):
    """Transactional outbox for broker delivery, per CONTRACTS.md §1/§5 (v1.13).

    One row per analysis job, written atomically with its `analysis_jobs` row
    by `upload_document`. The relay (app/relay.py), not the API, owns broker
    delivery -- see migration 011 for the RLS/grants model (no RLS; app_user
    INSERT-only, relay SELECT+UPDATE-only).
    """

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_jobs.id"), nullable=False, unique=True)
    # Opaque to the API (CONTRACTS.md §1) -- {"task_name": ..., "args": [...]},
    # an agreement purely between this writer and app/relay.py's reader.
    payload_json: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

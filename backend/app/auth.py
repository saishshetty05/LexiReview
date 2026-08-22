"""Authentication: register, login, logout (stateless), delete-account cascade.

Password hashing via passlib/bcrypt. JWT access tokens (pyjwt), delivered as
an HttpOnly+Secure+SameSite=Strict cookie by app/main.py -- NEVER localStorage
(SEC-6). Logout is client-side token discard; there is no server-side session
table for a stateless JWT (constitution/task scope, this PR).

TODO: refresh-token strategy is out of scope for this PR. Access tokens are
short-lived (15 min); once expired, the client must log in again. A refresh
token (rotating, stored server-side or as a second HttpOnly cookie) should be
added before this ships to real users, so a 15-minute session isn't the
actual UX.

MFA (TOTP) — opt-in, never mandatory. Uses pyotp for TOTP generation/
verification, qrcode[pil] for QR image. mfa_pending token is a short-lived
JWT (5 min) with custom claim mfa_pending=true; only valid for the
/auth/mfa/challenge endpoint, never for get_current_user.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
from passlib.hash import bcrypt
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.db import _normalize, app_user_session
from app.models import User

ACCESS_TOKEN_TTL_MINUTES = 15
JWT_ALGORITHM = "HS256"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8
# bcrypt silently truncates input beyond 72 bytes -- two passwords differing
# only after byte 72 would hash identically. Reject rather than mis-hash.
_MAX_PASSWORD_LEN = 72


class AuthError(Exception):
    """Base for typed auth errors. category+message only -- never a password
    or token value (CLAUDE.md rule 5/10).
    """

    category: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidEmailError(AuthError):
    category = "invalid_email"


class InvalidPasswordError(AuthError):
    category = "invalid_password"


class EmailAlreadyRegisteredError(AuthError):
    category = "email_already_registered"


class InvalidCredentialsError(AuthError):
    category = "invalid_credentials"


class RateLimitedError(AuthError):
    category = "rate_limited"


class InvalidTokenError(AuthError):
    category = "invalid_token"


# ── MFA errors ────────────────────────────────────────────────────────────────

class MFARequiredError(AuthError):
    """Raised by login when the user has MFA enabled — caller must complete
    the challenge step before receiving an access token."""
    category = "mfa_required"


class InvalidMFACodeError(AuthError):
    category = "invalid_mfa_code"


class MFARateLimitedError(AuthError):
    category = "mfa_rate_limited"


def _validate_email(email: str) -> None:
    if not _EMAIL_RE.match(email):
        raise InvalidEmailError("email is not a valid address")


def _validate_password(password: str) -> None:
    length = len(password.encode("utf-8"))
    if length < _MIN_PASSWORD_LEN or length > _MAX_PASSWORD_LEN:
        raise InvalidPasswordError(
            f"password must be {_MIN_PASSWORD_LEN}-{_MAX_PASSWORD_LEN} bytes"
        )


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.verify(password, password_hash)


def create_access_token(user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    """Returns the user_id encoded in `token`. Raises InvalidTokenError for
    any missing/expired/tampered/malformed token -- never distinguishes the
    reason in the response (constitution rule 2 applies to error detail as
    much as to logs: no token value is ever echoed back).
    """
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError("token is missing, expired, or invalid") from exc


# ── MFA (TOTP) helpers ──────────────────────────────────────────────────────

MFA_PENDING_TTL_MINUTES = 5


def generate_mfa_secret() -> str:
    """Generate a new base32-encoded TOTP secret."""
    return pyotp.random_base32()


def build_totp_uri(secret: str, email: str) -> str:
    """Build an otpauth:// URI for the QR code.

    Format: otpauth://totp/LexiReview:{email}?secret={secret}&issuer=LexiReview
    """
    return f"otpauth://totp/LexiReview:{email}?secret={secret}&issuer=LexiReview"


def verify_totp_code(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Verify a 6-digit TOTP code against the secret.

    valid_window=1 allows ±1 time step (30s each) for clock skew.
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=valid_window)


def create_mfa_pending_token(user_id: uuid.UUID) -> str:
    """Create a short-lived JWT for the MFA challenge flow.

    Claims: sub=user_id, mfa_pending=true, jti=unique_id, exp=now+5min
    This token is ONLY valid for /auth/mfa/challenge — get_current_user
    explicitly rejects it.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "mfa_pending": True,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=MFA_PENDING_TTL_MINUTES),
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)


def decode_mfa_pending_token(token: str) -> uuid.UUID:
    """Validate and decode an mfa_pending token.

    Raises InvalidTokenError if missing, expired, tampered, or missing
    the mfa_pending=true claim. Returns the user_id on success.
    """
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
        if payload.get("mfa_pending") is not True:
            raise InvalidTokenError("token is missing, expired, or invalid")
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError("token is missing, expired, or invalid") from exc


# ── Login rate limiting ────────────────────────────────────────────────────
# Crude in-memory counter, per-process only (does not survive a restart and
# is not shared across worker/API replicas). Good enough to blunt a naive
# credential-stuffing script hitting a single process; NOT a substitute for
# a real distributed limiter (e.g. Redis-backed) before this handles
# internet-facing traffic at scale.
_LOGIN_ATTEMPT_LIMIT = 5
_LOGIN_ATTEMPT_WINDOW_SECONDS = 300
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _check_login_rate_limit(email: str) -> None:
    now = time.monotonic()
    window_start = now - _LOGIN_ATTEMPT_WINDOW_SECONDS
    attempts = [t for t in _login_attempts[email] if t > window_start]
    _login_attempts[email] = attempts
    if len(attempts) >= _LOGIN_ATTEMPT_LIMIT:
        raise RateLimitedError("too many login attempts, try again later")


def _record_login_attempt(email: str) -> None:
    _login_attempts[email].append(time.monotonic())


def register(email: str, password: str) -> User:
    _validate_email(email)
    _validate_password(password)
    new_id = uuid.uuid4()
    password_hash = hash_password(password)
    try:
        with app_user_session(new_id) as session:
            user = User(id=new_id, email=email, password_hash=password_hash)
            session.add(user)
            session.flush()
    except IntegrityError as exc:
        raise EmailAlreadyRegisteredError("email is already registered") from exc
    return user


def authenticate(email: str, password: str) -> User:
    """Verify credentials, rate-limited per email. Raises
    InvalidCredentialsError for both "no such user" and "wrong password" --
    a single outcome, same as storage.py's anti-enumeration design, so a
    failed login can't be used to enumerate registered emails.
    """
    _check_login_rate_limit(email)
    _record_login_attempt(email)

    # email_lookup (migration 003) is the only RLS policy in the schema that
    # doesn't depend on app.user_id -- see its docstring and db.py's
    # app_user_session(None) docstring for why this is safe here and nowhere
    # else.
    with app_user_session(None) as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("invalid email or password")
    return user


@dataclass(frozen=True)
class CascadeResult:
    documents_deleted: int
    analysis_jobs_deleted: int
    analysis_results_deleted: int
    document_summaries_deleted: int
    decisions_deleted: int
    audit_log_rows_nulled: int


def delete_account_cascade(user_id: uuid.UUID) -> CascadeResult:
    """Hard-deletes every row owned by `user_id` (FR-15 / DPDP 24h SLA).

    !!! THE ONLY CODE PATH IN THIS PROJECT ALLOWED TO CONNECT AS THE OWNER
    ROLE (DATABASE_URL) INSTEAD OF app_user. !!!
    app_user has no DELETE grant on documents/analysis_jobs/analysis_results
    /document_summaries/decisions/audit_log/users BY DESIGN (migration
    001/002/006, docs/DECISION_LOG.md 2026-07-15) -- that is what makes
    every other table immutable at the DB layer, not just in app code (note:
    decisions is mutable via UPDATE, per migration 006, but still has no
    DELETE grant). This function bypasses that deliberately, via a separate
    privileged connection, because account deletion is the one place
    immutability must yield to a legal SLA. Do NOT copy this pattern
    anywhere else; every other read/write in this codebase must go through
    app_user_session.

    audit_log rows are NOT deleted (content-free per CLAUDE.md rule 2, IPs
    purged separately after 90 days per FR-8) -- only their user_id
    reference is nulled, so the row survives as an anonymous audit trail
    entry.

    Deletes in FK-safe order, in one transaction: decisions ->
    analysis_results -> document_summaries -> analysis_jobs -> documents ->
    (null audit_log.user_id) -> users. `decisions` goes first since it FKs
    into analysis_results.id (CONTRACTS.md §7, migration 006).
    """
    # NOTE: this reads the raw DATABASE_URL env var directly -- NOT
    # app.db.DATABASE_URL, which despite the name is the app's *resolved
    # connection* URL (APP_DATABASE_URL/app_user when set). Importing that
    # here would silently run this cascade as app_user, which has no DELETE
    # grant and would fail (or worse, on a future grant change, "succeed"
    # without actually being the privileged owner connection this function
    # exists to be).
    owner_url = os.environ["DATABASE_URL"]
    owner_engine = create_engine(_normalize(owner_url))
    try:
        with owner_engine.begin() as conn:
            decisions_deleted = conn.execute(
                text("DELETE FROM decisions WHERE user_id = :uid"), {"uid": user_id}
            ).rowcount
            results_deleted = conn.execute(
                text("DELETE FROM analysis_results WHERE user_id = :uid"), {"uid": user_id}
            ).rowcount
            summaries_deleted = conn.execute(
                text("DELETE FROM document_summaries WHERE user_id = :uid"), {"uid": user_id}
            ).rowcount
            jobs_deleted = conn.execute(
                text("DELETE FROM analysis_jobs WHERE user_id = :uid"), {"uid": user_id}
            ).rowcount
            documents_deleted = conn.execute(
                text("DELETE FROM documents WHERE user_id = :uid"), {"uid": user_id}
            ).rowcount
            audit_nulled = conn.execute(
                text("UPDATE audit_log SET user_id = NULL WHERE user_id = :uid"), {"uid": user_id}
            ).rowcount
            conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    finally:
        owner_engine.dispose()

    return CascadeResult(
        documents_deleted=documents_deleted,
        analysis_jobs_deleted=jobs_deleted,
        analysis_results_deleted=results_deleted,
        document_summaries_deleted=summaries_deleted,
        decisions_deleted=decisions_deleted,
        audit_log_rows_nulled=audit_nulled,
    )

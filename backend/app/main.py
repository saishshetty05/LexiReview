"""FastAPI app — grows per SETUP_GUIDE.md Phase 5.

main.py boundary (per A/B agreement): auth/upload routes are A's, job/findings
routes are B's. Same file, don't pull each other's in-progress branches.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from typing import Iterator, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    ACCESS_TOKEN_TTL_MINUTES,
    REFRESH_TOKEN_TTL_DAYS,
    AuthError,
    InvalidTokenError,
    MFARateLimitedError,
    authenticate,
    build_totp_uri,
    create_access_token,
    create_mfa_pending_token,
    create_refresh_token,
    decode_access_token,
    decode_mfa_pending_token,
    delete_account_cascade,
    generate_mfa_secret,
    register,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_totp_code,
)
from app.mfa_redis import check_and_increment_challenge_attempt, clear_challenge_attempts
from app.db import app_user_session
from app.decisions import UNSET, get_decisions_for_findings, upsert_decision
from app.jobs import create_queued_job
from app.models import AnalysisJob, AnalysisResult, AuditLog, Document, DocumentSummary, Outbox, User
from app.preflight import PreflightResult, run_preflight
from app.storage import (
    DocumentNotFoundError,
    ObjectMissingError,
    VersionMismatchError,
    fetch_document,
    put_document,
)

app = FastAPI(title="LexiReview API")

ACCESS_TOKEN_COOKIE = "access_token"  # nosec B105 -- cookie name, not a credential
REFRESH_TOKEN_COOKIE = "refresh_token"  # nosec B105 -- cookie name, not a credential
# CONTRACTS.md §9 (v1.11): scoped narrower than the access-token cookie's
# default "/" path -- the refresh token is only ever sent to the one
# endpoint that consumes it, not attached to every request. Revocation
# rides the *same* narrow path: /auth/refresh/revoke (below) is consumed via
# RFC 6265 prefix matching, so the cookie attached here is still delivered
# there without ever broadening the path to other /auth/* routes.
REFRESH_TOKEN_COOKIE_PATH = "/auth/refresh"

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

# category -> HTTP status for AuthError subclasses raised by app.auth.
_AUTH_ERROR_STATUS = {
    "invalid_email": 400,
    "invalid_password": 400,
    "email_already_registered": 409,
    "invalid_credentials": 401,
    "rate_limited": 429,
    "invalid_token": 401,
    "mfa_required": 200,      # not an error — login returns mfa_required flag
    "invalid_mfa_code": 401,
    "mfa_rate_limited": 429,
    "account_suspended": 403,
    "admin_required": 403,
}


def _auth_error_response(exc: AuthError) -> HTTPException:
    status_code = _AUTH_ERROR_STATUS.get(exc.category, 400)
    return HTTPException(status_code=status_code, detail={"category": exc.category})


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


# ── MFA request/response models ────────────────────────────────────────────

class MFASetupResponse(BaseModel):
    qr_code_base64: str
    # This is the raw TOTP secret (base32) for manual entry into authenticator apps.
    # It is NOT a one-time recovery/backup code. Named 'totp_secret' to avoid confusion.
    totp_secret: str


class MFAVerifySetupRequest(BaseModel):
    code: str  # 6-digit TOTP


class MFADisableRequest(BaseModel):
    code: str  # 6-digit TOTP


class MFAChallengeRequest(BaseModel):
    mfa_token: str
    code: str  # 6-digit TOTP


def _set_access_cookie(response: Response, token: str) -> None:
    # Secure cookies are dropped silently by browsers over plain HTTP
    # (Vite dev serves http://localhost:5173) -- login would 200 but the
    # cookie wouldn't stick, and everything after would 401. Fail-safe
    # default is secure=True (production-safe); only local compose dev
    # explicitly opts out via ENVIRONMENT=development (set in
    # docker-compose.yml, not requiring a manual .env edit). Found in PR
    # #39 review, flagged as out of scope there since app/auth.py is A's file.
    is_dev = os.environ.get("ENVIRONMENT") == "development"
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        token,
        httponly=True,
        secure=not is_dev,
        samesite="strict",
        max_age=ACCESS_TOKEN_TTL_MINUTES * 60,
    )


def _set_refresh_cookie(response: Response, token: str) -> None:
    is_dev = os.environ.get("ENVIRONMENT") == "development"
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        token,
        httponly=True,
        secure=not is_dev,
        samesite="strict",
        max_age=REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
        path=REFRESH_TOKEN_COOKIE_PATH,
    )


def get_current_user(request: Request) -> Iterator[tuple[User, Session]]:
    """Dependency for every protected endpoint: validates the JWT cookie and
    holds a request-scoped app_user_session open for its lifetime -- this is
    the enforcement point for constitution rule 1 (every query user_id-scoped)
    on the API side.
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail={"category": "missing_token"})
    try:
        user_id = decode_access_token(token)
    except InvalidTokenError as exc:
        raise _auth_error_response(exc) from exc
    with app_user_session(user_id) as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail={"category": "invalid_token"})
        # Account suspension (PR #2 of 3): get_current_user loads the User row
        # fresh from the DB on every request, so this is the single enforcement
        # point where an admin demotion/suspension takes effect -- no JWT claim
        # check needed (a suspended user's cached token is invalidated by the
        # next request loading active=FALSE here, not by anything in the JWT
        # itself).
        if not user.active:
            raise HTTPException(
                status_code=403, detail={"category": "account_suspended"}
            )
        yield user, session


def require_admin(
    current: tuple[User, Session] = Depends(get_current_user),
) -> tuple[User, Session]:
    """Dependency for admin-only endpoints (PR #2 of 3): chains on
    get_current_user (so it also enforces authentication + the active check)
    and rejects non-admins.

    Reads is_admin off the User row get_current_user just loaded from the DB
    every request -- deliberately NOT a JWT admin claim. A demoted admin's
    cached token is therefore invalidated on their next request (the fresh
    User row has is_admin=FALSE), which is exactly the "takes effect
    immediately" property the design locked in. The SECURITY DEFINER functions
    (migration 013) additionally re-check is_admin inside the DB, so even a
    direct SQL call can't escalate.
    """
    user, session = current
    if not user.is_admin:
        raise HTTPException(status_code=403, detail={"category": "admin_required"})
    yield user, session


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/register", status_code=201)
def auth_register(body: RegisterRequest) -> dict:
    try:
        user = register(body.email, body.password)
    except AuthError as exc:
        raise _auth_error_response(exc) from exc
    return {"user_id": str(user.id), "email": user.email}


@app.post("/auth/login")
def auth_login(body: LoginRequest, response: Response) -> dict:
    try:
        user = authenticate(body.email, body.password)
    except AuthError as exc:
        raise _auth_error_response(exc) from exc

    # MFA-enabled user: return mfa_pending token instead of access token
    if user.mfa_enabled:
        mfa_token = create_mfa_pending_token(user.id)
        return {"mfa_required": True, "mfa_token": mfa_token}

    # Non-MFA user: normal flow
    token = create_access_token(user.id)
    _set_access_cookie(response, token)
    refresh_token = create_refresh_token(user.id)
    _set_refresh_cookie(response, refresh_token)
    return {"user_id": str(user.id)}


@app.get("/auth/me")
def auth_me(current: tuple[User, Session] = Depends(get_current_user)) -> dict:
    """CONTRACTS.md §3(c): the current user's own identity, including
    is_admin -- there was previously no server-truth endpoint for this at
    all (useAuth.tsx's own docstring flagged the gap), so the frontend had
    no way to know whether to show admin-only UI. Reuses get_current_user
    (auth + active check), no new RLS surface -- self-only, same as every
    other user-scoped read.
    """
    user, _ = current
    return {"user_id": str(user.id), "email": user.email, "is_admin": user.is_admin}


@app.post("/auth/mfa/setup")
def auth_mfa_setup(current: tuple[User, Session] = Depends(get_current_user)) -> MFASetupResponse:
    """Generate a new TOTP secret and return QR code + backup code.

    Does NOT enable MFA yet — caller must complete /auth/mfa/verify-setup
    with a valid TOTP code first. This proves the authenticator app works
    before we lock MFA on.
    """
    user, session = current
    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail={"category": "mfa_already_enabled"})

    secret = generate_mfa_secret()
    # Store secret temporarily (mfa_enabled still FALSE)
    user.mfa_secret = secret
    session.flush()

    # Generate QR code as base64 PNG
    import base64
    import io
    import qrcode

    uri = build_totp_uri(secret, user.email)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return MFASetupResponse(qr_code_base64=qr_base64, totp_secret=secret)


@app.get("/auth/mfa/status")
def auth_mfa_status(
    current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """Return current MFA status for the authenticated user."""
    user, _ = current
    return {
        "mfa_enabled": user.mfa_enabled,
        "mfa_configured": user.mfa_secret is not None,
    }


@app.post("/auth/mfa/verify-setup")
def auth_mfa_verify_setup(
    body: MFAVerifySetupRequest,
    current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """Verify the TOTP code and enable MFA if valid."""
    user, session = current
    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail={"category": "mfa_already_enabled"})
    if not user.mfa_secret:
        raise HTTPException(status_code=400, detail={"category": "mfa_not_configured"})

    if not verify_totp_code(user.mfa_secret, body.code):
        raise HTTPException(status_code=401, detail={"category": "invalid_mfa_code"})

    user.mfa_enabled = True
    session.flush()
    return {"status": "mfa_enabled"}


@app.post("/auth/mfa/disable")
def auth_mfa_disable(
    body: MFADisableRequest,
    current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """Disable MFA — requires a valid TOTP code to prove possession."""
    user, session = current
    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail={"category": "mfa_not_enabled"})
    if not user.mfa_secret:
        raise HTTPException(status_code=400, detail={"category": "mfa_not_configured"})

    if not verify_totp_code(user.mfa_secret, body.code):
        raise HTTPException(status_code=401, detail={"category": "invalid_mfa_code"})

    user.mfa_enabled = False
    user.mfa_secret = None
    session.flush()
    return {"status": "mfa_disabled"}


@app.post("/auth/mfa/challenge")
def auth_mfa_challenge(body: MFAChallengeRequest, response: Response) -> dict:
    """Exchange a valid mfa_pending token + TOTP code for a real access token.

    NOT protected by get_current_user — the mfa_pending token itself
    authenticates the request. Rate-limited to 5 attempts per token.
    """
    # Decode and validate mfa_pending token using shared helper
    try:
        user_id, jti = decode_mfa_pending_token(body.mfa_token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail={"category": "invalid_token"}) from exc

    # Rate limit: max 5 attempts per mfa_pending token
    try:
        check_and_increment_challenge_attempt(user_id, jti)
    except MFARateLimitedError as exc:
        raise _auth_error_response(exc) from exc

    # Fetch user (with RLS via app_user_session) to get mfa_secret
    with app_user_session(user_id) as session:
        user = session.get(User, user_id)
        if user is None or not user.mfa_enabled or not user.mfa_secret:
            raise HTTPException(status_code=401, detail={"category": "invalid_token"})

        if not verify_totp_code(user.mfa_secret, body.code):
            raise HTTPException(status_code=401, detail={"category": "invalid_mfa_code"})

    # Success — clear attempts and issue real access token
    clear_challenge_attempts(user_id, jti)
    token = create_access_token(user_id)
    _set_access_cookie(response, token)
    refresh_token = create_refresh_token(user_id)
    _set_refresh_cookie(response, refresh_token)
    return {"user_id": str(user_id)}


@app.post("/auth/refresh")
def auth_refresh(request: Request, response: Response) -> dict:
    """Exchange a valid refresh token for a fresh access token + a rotated
    refresh token (CONTRACTS.md §9). No get_current_user dependency — the
    whole point is to work once the access token has already expired.
    """
    token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail={"category": "invalid_token"})
    try:
        user_id, new_refresh_token = rotate_refresh_token(token)
    except InvalidTokenError as exc:
        response.delete_cookie(ACCESS_TOKEN_COOKIE)
        response.delete_cookie(REFRESH_TOKEN_COOKIE, path=REFRESH_TOKEN_COOKIE_PATH)
        raise _auth_error_response(exc) from exc

    access_token = create_access_token(user_id)
    _set_access_cookie(response, access_token)
    _set_refresh_cookie(response, new_refresh_token)
    return {"user_id": str(user_id)}


@app.post("/auth/refresh/revoke")
def auth_revoke_refresh_token(request: Request, response: Response) -> dict:
    """Revoke the presented refresh token server-side, then clear both
    cookies. No get_current_user dependency, same as POST /auth/refresh --
    the cookie itself is the credential; it has to work once the access token
    is gone. Its path automatically matches the narrow path=/auth/refresh
    cookie via RFC 6265 prefix matching (/auth/refresh is a prefix of
    /auth/refresh/revoke), so the refresh token is never attached to any other
    /auth/* route.
    """
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if refresh_token:
        revoke_refresh_token(refresh_token)
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path=REFRESH_TOKEN_COOKIE_PATH)
    return {"status": "logged_out"}


@app.post("/auth/logout")
def auth_logout(response: Response) -> dict:
    # Client-side cookie clear only, same as the stateless-JWT design always
    # was. Server-side revocation of the refresh token is POST
    # /auth/refresh/revoke, which the frontend calls before this -- the
    # refresh_token cookie is path-scoped to /auth/refresh, so RFC 6265 never
    # attaches it to /auth/logout, and a revoke here would always read None
    # (DECISION_LOG 2026-08-28; CONTRACTS.md §9).
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path=REFRESH_TOKEN_COOKIE_PATH)
    return {"status": "logged_out"}


@app.delete("/auth/account")
def auth_delete_account(
    response: Response, current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    user, _session = current
    user_id: uuid.UUID = user.id
    delete_account_cascade(user_id)
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path=REFRESH_TOKEN_COOKIE_PATH)
    return {"status": "deleted"}


# ── Admin endpoints (PR #3 of 3) ─────────────────────────────────────────
# CONTRACTS.md §10: /admin/users — user management, metadata only (never
# document content). Both endpoints are gated by require_admin, which chains
# on get_current_user (auth + non-suspended + is_admin, each re-checked fresh
# from the DB every request — no JWT admin claim, so a suspension or demotion
# takes effect on the admin's very next request).
#
# The cross-user access these endpoints need is provided by the SECURITY
# DEFINER functions from migration 013, not by a second RLS policy: the bypass
# lives inside the DB function, which re-verifies the caller's is_admin itself,
# so admin scope is enforced at the database even if the app layer ever made a
# mistake (PR #2 review rationale).


class AdminSetUserActiveRequest(BaseModel):
    active: bool


def _admin_set_user_active_exception(exc: Exception) -> HTTPException | None:
    """Map the plpgsql errors raised by admin_set_user_active() (migration
    013) to HTTP responses. The function fails closed by design — the access
    bypass and the authz check both live inside the DB — so a caller reaching
    one of these through the endpoint would mean the app layer and the DB
    disagreed about the admin's privileges (defensive; require_admin makes
    them impossible in the normal path). Anything unmapped propagates loudly
    rather than being masked as a generic error.
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    sqlstate = getattr(diag, "sqlstate", None)
    # P0002 is PostgreSQL's PL/pgSQL-class no_data_found (what RAISE
    # ... USING ERRCODE='no_data_found' actually yields) — NOT the 02-class
    # 02000; the migration's function raises the former. Found by running the
    # endpoint against the real function during PR #3 development.
    if sqlstate == "P0002":  # no_data_found — target user does not exist
        return HTTPException(status_code=404, detail={"category": "user_not_found"})
    if sqlstate == "23514":  # check_violation — admin tried to suspend self
        return HTTPException(status_code=400, detail={"category": "cannot_self_suspend"})
    return None


@app.get("/admin/users")
def admin_list_users_endpoint(
    current: tuple[User, Session] = Depends(require_admin),
) -> list[dict]:
    """CONTRACTS.md §10: list all users (metadata only), in created_at order.
    Calls the SECURITY DEFINER admin_list_users() (migration 013), which
    returns ONLY the safe columns (id, email, is_admin, active, created_at) —
    password_hash and mfa_secret never leave the DB (the safe-columns fix from
    PR #2 review lives in the function, so this endpoint can't leak them no
    matter how it serializes the rows).
    """
    _admin, session = current
    rows = session.execute(text("SELECT * FROM admin_list_users()")).mappings().all()
    return [
        {
            "user_id": str(row["id"]),
            "email": row["email"],
            "is_admin": row["is_admin"],
            "active": row["active"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


@app.patch("/admin/users/{user_id}")
def admin_set_user_active_endpoint(
    user_id: uuid.UUID,
    body: AdminSetUserActiveRequest,
    current: tuple[User, Session] = Depends(require_admin),
) -> dict:
    """CONTRACTS.md §10: suspend (active=False) or reactivate (active=True) a
    target user. Delegates to the SECURITY DEFINER admin_set_user_active()
    (migration 013), which refuses self-suspension and unknown targets inside
    the DB.

    No token revocation is needed here: get_current_user (which require_admin
    chains on) loads the target's User row fresh from the DB on every request,
    so the very next request a suspended user makes is rejected with 403
    account_suspended — active=FALSE takes effect immediately and cannot be
    papered over by a cached JWT.
    """
    admin, session = current
    try:
        session.execute(
            text("SELECT admin_set_user_active(:target, :active)"),
            {"target": user_id, "active": body.active},
        )
    except Exception as exc:
        mapped = _admin_set_user_active_exception(exc)
        if mapped is not None:
            raise mapped from exc
        raise
    # Audit (CLAUDE.md rule 2): metadata only — event_type + acting admin +
    # target. audit_log's FORCE RLS (user_id = app.user_id) passes because the
    # acting user IS app.user_id; target_user_id rides the migration-012 column.
    session.add(
        AuditLog(
            event_type="user_suspended" if not body.active else "user_reactivated",
            user_id=admin.id,
            target_user_id=user_id,
        )
    )
    return {"user_id": str(user_id), "active": body.active}


def _parse_error_reason(error_reason: str) -> dict:
    # jobs._format_error always writes "category: message" -- partition on
    # the first ": " to invert it back into the contract's {category, message}.
    category, _, message = error_reason.partition(": ")
    return {"category": category, "message": message}


@app.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID, current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """CONTRACTS.md §3(a) polling shape. No document text here -- only in
    /jobs/{id}/findings. A job_id that doesn't exist and one that exists but
    isn't owned by the current user both 404 identically: RLS (the
    app_user_session opened by get_current_user) is what makes session.get
    return None either way, the same anti-enumeration-by-construction
    pattern as storage.fetch_document (CONTRACTS.md §4) -- not two branches
    that happen to return the same thing.
    """
    _user, session = current
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": str(job.id),
        "state": job.state,
        "retry_count": job.retry_count,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": _parse_error_reason(job.error_reason) if job.error_reason else None,
    }


@app.get("/jobs/{job_id}/findings")
def get_job_findings(
    job_id: uuid.UUID, current: tuple[User, Session] = Depends(get_current_user)
) -> list[dict]:
    """CONTRACTS.md §3(a): verified findings first, then unverified, each
    group sorted by severity (high -> info). Empty list for a job with no
    results yet (queued/running) -- not an error, just nothing to show.
    Same anti-enumeration 404 as get_job.

    CONTRACTS.md §7 (v1.7): each returned object is the stored Findings JSON
    (§2, unchanged) plus two envelope fields -- `finding_id` (the stable
    analysis_results.id, since array position alone is too fragile to key a
    decision on) and `decision` ("pending" if the caller has never recorded
    one for this finding).
    """
    user, session = current
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    results = (
        session.execute(select(AnalysisResult).where(AnalysisResult.job_id == job_id))
        .scalars()
        .all()
    )
    verified = sorted(
        (r for r in results if r.verification == "verified"),
        key=lambda r: _SEVERITY_ORDER.get(r.severity, len(_SEVERITY_ORDER)),
    )
    unverified = sorted(
        (r for r in results if r.verification != "verified"),
        key=lambda r: _SEVERITY_ORDER.get(r.severity, len(_SEVERITY_ORDER)),
    )
    ordered = (*verified, *unverified)
    decisions = get_decisions_for_findings(session, user_id=user.id, finding_ids=[r.id for r in ordered])
    return [
        {
            **r.payload,
            "finding_id": str(r.id),
            "decision": decisions[r.id].decision if r.id in decisions else "pending",
            # CONTRACTS.md §7a (v1.8): null when the caller has never set one.
            "severity_override": decisions[r.id].severity_override if r.id in decisions else None,
        }
        for r in ordered
    ]


class DecisionRequest(BaseModel):
    decision: Literal["pending", "accepted", "dismissed"]
    # CONTRACTS.md §7a (v1.8): absent (default) leaves an existing override
    # untouched; explicit null clears it; a value sets it. The route (not
    # this default) is what tells "absent" apart from "sent as null" --
    # see put_finding_decision's use of `model_fields_set`.
    severity_override: Literal["high", "medium", "low", "info"] | None = None


@app.put("/jobs/{job_id}/findings/{finding_id}/decision")
def put_finding_decision(
    job_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: DecisionRequest,
    current: tuple[User, Session] = Depends(get_current_user),
) -> dict:
    """CONTRACTS.md §7: upserts the caller's decision for one finding.
    finding_id must belong to job_id -- checked explicitly, since RLS alone
    only proves the finding belongs to the caller, not to this job. Same
    anti-enumeration 404 as get_job/get_job_findings either way.

    CONTRACTS.md §7a (v1.8): severity_override is UNSET unless the caller's
    JSON body actually included the key (model_fields_set) -- a plain
    Optional[...] = None default couldn't distinguish "not sent" from "sent
    as null," and those mean different things to upsert_decision.
    """
    user, session = current
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    finding = session.get(AnalysisResult, finding_id)
    if finding is None or finding.job_id != job_id:
        raise HTTPException(status_code=404, detail="finding not found")
    severity_override = (
        body.severity_override if "severity_override" in body.model_fields_set else UNSET
    )
    row = upsert_decision(
        session,
        user_id=user.id,
        finding_id=finding_id,
        decision=body.decision,
        severity_override=severity_override,
    )
    return {
        "finding_id": str(row.finding_id),
        "decision": row.decision,
        "severity_override": row.severity_override,
    }


@app.get("/documents/{document_id}/summary")
def get_document_summary(
    document_id: uuid.UUID, current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """CONTRACTS.md §2b: "the document's current version and the pinned
    model_version." Two-step lookup, both RLS-scoped through the same
    session:

    1. Resolve the CURRENT version of document_id (highest `version`,
       documents.doc_id/version pair is immutable per row -- CLAUDE.md rule
       9 -- so "current" just means most recent version row). This step is
       also the anti-enumeration 404: a nonexistent document_id and one
       owned by someone else both produce zero visible rows via RLS -- one
       branch, one 404, same pattern as get_job/storage.fetch_document.
    2. Query document_summaries filtered to that version's doc_version_hash
       -- NOT any doc_version_hash ever seen for this doc_id. A prior
       version without a matching bugfix here would silently serve a STALE
       summary after a re-upload that hasn't been re-analyzed yet (caught
       in PR #33 review). If LLM_MODEL is set, also filter to that pinned
       model_version, per the contract wording; if unset (e.g. a dev
       environment before the Console key exists), fall back to the most
       recent summary at that hash by created_at -- documented here rather
       than silently picking an arbitrary row.
    """
    _user, session = current
    current_version = (
        session.execute(
            select(Document)
            .where(Document.doc_id == document_id)
            .order_by(Document.version.desc())
        )
        .scalars()
        .first()
    )
    if current_version is None:
        raise HTTPException(status_code=404, detail="document summary not found")

    summary_query = select(DocumentSummary).where(
        DocumentSummary.doc_version_hash == current_version.doc_version_hash
    )
    pinned_model_version = os.environ.get("LLM_MODEL")
    if pinned_model_version:
        summary_query = summary_query.where(
            DocumentSummary.model_version == pinned_model_version
        )
    summary_query = summary_query.order_by(DocumentSummary.created_at.desc())

    summary = session.execute(summary_query).scalars().first()
    if summary is None:
        raise HTTPException(status_code=404, detail="document summary not found")
    return summary.payload


@app.get("/documents")
def list_documents(
    current: tuple[User, Session] = Depends(get_current_user)
) -> list[dict]:
    """Returns the 5 most recent documents for the current user, each with
    its latest analysis job state. Ordered by document creation time descending.

    Response shape:
    [
      {
        "doc_id": "...",
        "original_filename": "...",
        "file_type": "pdf" | "docx",
        "page_count": 10,
        "size_bytes": 12345,
        "created_at": "...",
        "latest_job": {
          "job_id": "...",
          "state": "succeeded" | "failed" | "queued" | "running",
          "created_at": "...",
          "finished_at": "..." | null
        } | null
      },
      ...
    ]
    """
    _user, session = current
    # RLS scopes this query automatically via app_user_session
    stmt = (
        select(Document)
        .order_by(Document.created_at.desc())
        .limit(5)
    )
    documents = session.execute(stmt).scalars().all()

    result = []
    for doc in documents:
        # Get the most recent job for this specific document version.
        # Scope by doc_version_hash to avoid showing v2's job on a v1 card
        # when a document has been re-uploaded (same doc_id, different hash).
        latest_job_stmt = (
            select(AnalysisJob)
            .where(AnalysisJob.doc_id == doc.doc_id)
            .where(AnalysisJob.doc_version_hash == doc.doc_version_hash)
            .order_by(AnalysisJob.created_at.desc())
            .limit(1)
        )
        latest_job = session.execute(latest_job_stmt).scalar_one_or_none()

        job_info = None
        if latest_job is not None:
            job_info = {
                "job_id": str(latest_job.id),
                "state": latest_job.state,
                "created_at": latest_job.created_at.isoformat(),
                "finished_at": latest_job.finished_at.isoformat() if latest_job.finished_at else None,
            }

        result.append({
            "doc_id": str(doc.doc_id),
            "original_filename": doc.original_filename,
            "file_type": doc.file_type,
            "page_count": doc.page_count,
            "size_bytes": doc.size_bytes,
            "created_at": doc.created_at.isoformat(),
            "latest_job": job_info,
        })

    return result


_FILE_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@app.get("/documents/{document_id}/file")
def get_document_file(
    document_id: uuid.UUID, current: tuple[User, Session] = Depends(get_current_user)
) -> Response:
    """Serves the raw file bytes for the document viewer (frontend commercial
    redesign). Reuses storage.fetch_document -- no new storage code, no
    schema change.

    Same two-step "resolve current version, then fetch by hash" pattern as
    get_document_summary immediately above: the current-version lookup is
    also the anti-enumeration 404 (a nonexistent document_id and one owned
    by someone else both produce zero visible rows via RLS -- one branch,
    one 404). fetch_document's own typed errors are re-raised as the same
    404 rather than leaking which failure mode occurred, for the same
    anti-enumeration reason CONTRACTS.md §4 documents for its callers.
    """
    _user, session = current
    current_version = (
        session.execute(
            select(Document)
            .where(Document.doc_id == document_id)
            .order_by(Document.version.desc())
        )
        .scalars()
        .first()
    )
    if current_version is None:
        raise HTTPException(status_code=404, detail="document not found")

    try:
        contents = fetch_document(document_id, _user.id, current_version.doc_version_hash)
    except (DocumentNotFoundError, VersionMismatchError, ObjectMissingError) as exc:
        raise HTTPException(status_code=404, detail="document not found") from exc

    content_type = _FILE_CONTENT_TYPES.get(current_version.file_type, "application/octet-stream")
    return Response(content=contents, media_type=content_type)


def _preflight_status_code(result: PreflightResult) -> int:
    # preflight.py returns a free-text reason + a metadata dict, not a
    # status-code enum -- map by which metadata keys are present, not by
    # brittle substring matching on the reason alone.
    reason = result.reason
    if "size_bytes" in result.metadata and "MB" in reason:
        return 413  # Payload Too Large
    if reason.startswith("Unsupported or unrecognized file type"):
        return 415  # Unsupported Media Type
    if "page_count" in result.metadata and "pages" in reason and "limit" in reason:
        return 413  # too many pages is also a size-class limit
    # Empty file, corrupted/encrypted PDF or DOCX, no extractable text,
    # context-limit exceeded: the file was readable but not processable.
    return 422  # Unprocessable Entity


@app.post("/documents/upload", status_code=201)
def upload_document(
    file: UploadFile = File(...), current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    """FR-6 / CLAUDE.md rule 8: preflight passes BEFORE any storage or DB
    write happens -- nothing below the preflight call executes on rejection.

    Broker-enqueue (CONTRACTS.md §1/§5, v1.13): the documents/analysis_jobs/
    outbox rows are all written atomically in the same app_user_session
    transaction below. This request makes no broker call at all -- delivery
    is deferred to the outbox relay (app/relay.py), so upload can never fail
    with a broker_failure 502; a broker outage just means delivery is
    delayed, not lost.

    Storage-failure handling: storage.put_document() is called INSIDE the
    same app_user_session block as the documents/analysis_jobs inserts, one
    commit at the end. If put_document raises, the exception propagates out
    of the `with` block and app_user_session's own except-clause rolls the
    transaction back -- no compensating DELETE is needed (and app_user has
    no DELETE grant on documents by design; only delete_account_cascade may
    ever use the owner connection). An orphaned documents row with no bytes
    in storage is worse than nothing: a later fetch_document/summary call
    would hit "found in DB, missing in storage" with no clean retry path.
    Rolling back lets the client just re-upload.

    FR-6 dedup: one documents.doc_version_hash match for the caller (RLS
    scopes the SELECT, no explicit user_id filter needed) returns 409 with
    the existing doc_id + its most recent job_id, so the client can poll
    /jobs/{job_id} instead of re-uploading. The SELECT-then-INSERT check
    above is the common-case path; a DB-level UNIQUE(user_id,
    doc_version_hash) constraint (migration 007) is the actual guarantee.
    If two concurrent uploads of the identical file by the same user both
    pass the SELECT, the loser's INSERT raises IntegrityError -- caught
    below, which re-runs the SELECT (now finding the winner's committed
    row) and returns the identical 409 body as the common case, so the
    race path is observationally indistinguishable from it.
    """
    user, _outer_session = current
    contents = file.file.read()

    # Mirrors extraction.py's per-job random temp dir pattern (CLAUDE.md
    # rule 5): all file I/O for preflight happens inside it, purged in a
    # finally block regardless of accept/reject.
    job_dir = tempfile.mkdtemp(prefix="lexireview-upload-")
    try:
        upload_path = os.path.join(job_dir, "upload")
        with open(upload_path, "wb") as f:
            f.write(contents)
        result = run_preflight(upload_path)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)

    if not result.accepted:
        raise HTTPException(
            status_code=_preflight_status_code(result),
            detail={"category": "preflight_rejected", "message": result.reason},
        )

    sha256_hash = result.metadata["sha256"]
    file_type = result.metadata["file_type"]
    page_count = result.metadata.get("page_count")
    size_bytes = result.metadata["size_bytes"]
    # original_filename is sanitized-at-upload text only (models.py's
    # Document docstring) -- basename strips any path/traversal components.
    sanitized_filename = os.path.basename(file.filename or "upload")

    def _duplicate_document_response(existing_doc: Document, session: Session) -> HTTPException:
        existing_job = (
            session.execute(
                select(AnalysisJob)
                .where(AnalysisJob.doc_id == existing_doc.doc_id)
                .order_by(AnalysisJob.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return HTTPException(
            status_code=409,
            detail={
                "category": "duplicate_document",
                "message": "A document with this content has already been uploaded.",
                "doc_id": str(existing_doc.doc_id),
                "job_id": str(existing_job.id) if existing_job else None,
            },
        )

    with app_user_session(user.id) as session:
        existing = (
            session.execute(select(Document).where(Document.doc_version_hash == sha256_hash))
            .scalars()
            .first()
        )
        if existing is not None:
            raise _duplicate_document_response(existing, session)

        doc_id = uuid.uuid4()
        document = Document(
            doc_id=doc_id,
            user_id=user.id,
            version=1,
            doc_version_hash=sha256_hash,
            original_filename=sanitized_filename,
            file_type=file_type,
            page_count=page_count,
            size_bytes=size_bytes,
            is_synthetic=False,
        )
        session.add(document)
        try:
            # Flush now (rather than letting it ride to the block's final
            # commit) so a losing concurrent upload's unique-constraint
            # violation (migration 007) surfaces here, before the job row
            # or storage write happen -- not after.
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            # Defensive: this catch exists specifically for the FR-6 dedup
            # race (migration 007's uq_documents_user_version). If a future
            # migration adds another unique/check constraint reachable from
            # this same INSERT, its violation must not be misreported as a
            # duplicate-document 409 -- re-raise anything else unchanged.
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint != "uq_documents_user_version":
                raise

            # SET LOCAL is transaction-scoped (docs/DECISION_LOG.md,
            # 2026-07-20 / migration 005): rollback() ends the transaction
            # that set app.user_id, so it must be re-applied before the
            # session issues another RLS-scoped query, or the fail-closed
            # policy raises instead of finding the winner's row.
            session.execute(text(f"SET LOCAL app.user_id = '{uuid.UUID(str(user.id))}'"))
            existing = (
                session.execute(select(Document).where(Document.doc_version_hash == sha256_hash))
                .scalars()
                .first()
            )
            if existing is None:
                # Defensive: a unique-constraint violation on this exact
                # column pair with no matching row now would mean something
                # other than this race caused it. Don't swallow it as a 409.
                raise
            raise _duplicate_document_response(existing, session)

        job = create_queued_job(
            session, user_id=user.id, doc_id=doc_id, doc_version_hash=sha256_hash
        )
        job_id = job.id

        try:
            put_document(user.id, doc_id, 1, contents)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "category": "storage_failure",
                    "message": "Could not store the uploaded document. Please try again.",
                },
            ) from exc

        # Atomic with the rows above (CONTRACTS.md §1/§5, v1.13): the API
        # makes no broker call itself. Task name verified against worker.py's
        # @celery_app.task(bind=True, name="analyze_document") decorator --
        # it explicitly overrides the default dotted-path name. Pass-by-ID
        # only (CLAUDE.md rule 4): no document content crosses the queue.
        session.add(
            Outbox(
                job_id=job_id,
                payload_json={"task_name": "analyze_document", "args": [str(doc_id), str(user.id)]},
            )
        )

    return {"doc_id": str(doc_id), "job_id": str(job_id), "state": "queued"}

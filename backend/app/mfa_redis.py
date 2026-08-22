"""Redis-backed rate limiter for the MFA challenge endpoint.

Uses the same Redis instance as Celery (REDIS_URL env var). Keys are
scoped per mfa_pending token (via its jti claim) to prevent brute-force
attacks on a single challenge token. Max 5 attempts per token, then the
token is invalidated.
"""
from __future__ import annotations

import os
import uuid

import redis

from app.auth import MFA_PENDING_TTL_MINUTES

_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
_CLIENT: redis.Redis | None = None


def _client() -> redis.Redis:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = redis.from_url(_REDIS_URL, decode_responses=True)
    return _CLIENT


# ── Rate limiting for /auth/mfa/challenge ──────────────────────────────────

# Key format: mfa_challenge:{user_id}:{jti} -> attempt count (int)
# TTL matches MFA_PENDING_TTL_MINUTES (5 min)
_MAX_ATTEMPTS = 5


def _challenge_key(user_id: str, jti: str) -> str:
    return f"mfa_challenge:{user_id}:{jti}"


def check_and_increment_challenge_attempt(user_id: uuid.UUID, jti: str) -> None:
    """Increment the attempt counter for this challenge token.

    Raises MFARateLimitedError if attempts exceed _MAX_ATTEMPTS.
    """
    from app.auth import MFARateLimitedError  # local import to avoid cycle

    client = _client()
    key = _challenge_key(str(user_id), jti)
    # INCR returns the new value after increment
    attempts = client.incr(key)
    if attempts == 1:
        # First attempt — set TTL
        client.expire(key, MFA_PENDING_TTL_MINUTES * 60)
    if attempts > _MAX_ATTEMPTS:
        # Rate limited — keep the key so subsequent attempts also fail
        # (don't delete, just raise). The TTL will expire it naturally.
        raise MFARateLimitedError("too many MFA attempts, request a new login challenge")


def clear_challenge_attempts(user_id: uuid.UUID, jti: str) -> None:
    """Clear the attempt counter on successful challenge."""
    client = _client()
    key = _challenge_key(str(user_id), jti)
    client.delete(key)


def get_challenge_attempts(user_id: uuid.UUID, jti: str) -> int:
    """Get current attempt count (for testing/debugging)."""
    client = _client()
    key = _challenge_key(str(user_id), jti)
    val = client.get(key)
    return int(val) if val else 0
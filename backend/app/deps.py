"""FastAPI dependencies shared across routes.

get_current_user_id is a placeholder: CLAUDE.md rule 1 requires every query
scoped by user_id, but there's no way to derive "the current user" without
real auth, which is Saish's in-progress lane (main.py boundary: his routes,
my routes, same file). Reads a raw X-User-Id header -- NOT a real auth
boundary, just a seam. Every route using this dependency already scopes its
query through app_user_session(user_id), so swapping this implementation
for real JWT verification later doesn't require touching route bodies.
"""
from __future__ import annotations

import uuid

from fastapi import Header, HTTPException


def get_current_user_id(x_user_id: str = Header(...)) -> uuid.UUID:
    try:
        return uuid.UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid X-User-Id header") from exc

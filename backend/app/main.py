"""FastAPI app. Grows per SETUP_GUIDE.md Phase 5."""
from __future__ import annotations

import uuid
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import (
    ACCESS_TOKEN_TTL_MINUTES,
    AuthError,
    InvalidTokenError,
    authenticate,
    create_access_token,
    decode_access_token,
    delete_account_cascade,
    register,
)
from app.db import app_user_session
from app.models import User

app = FastAPI(title="LexiReview API")

ACCESS_TOKEN_COOKIE = "access_token"  # nosec B105 -- cookie name, not a credential

# category -> HTTP status for AuthError subclasses raised by app.auth.
_AUTH_ERROR_STATUS = {
    "invalid_email": 400,
    "invalid_password": 400,
    "email_already_registered": 409,
    "invalid_credentials": 401,
    "rate_limited": 429,
    "invalid_token": 401,
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


def _set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=ACCESS_TOKEN_TTL_MINUTES * 60,
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
    token = create_access_token(user.id)
    _set_access_cookie(response, token)
    return {"user_id": str(user.id)}


@app.post("/auth/logout")
def auth_logout(response: Response) -> dict:
    # Stateless JWT: logout is client-side token discard. Clearing the
    # cookie here covers the browser client; any other holder of the token
    # remains valid until it expires (ACCESS_TOKEN_TTL_MINUTES) -- no server
    # session table in this PR (see app/auth.py module docstring TODO).
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    return {"status": "logged_out"}


@app.delete("/auth/account")
def auth_delete_account(
    response: Response, current: tuple[User, Session] = Depends(get_current_user)
) -> dict:
    user, _session = current
    user_id: uuid.UUID = user.id
    delete_account_cascade(user_id)
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    return {"status": "deleted"}

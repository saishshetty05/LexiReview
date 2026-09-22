"""CORS accepts a configurable set of origins (CORS_ORIGINS env var,
comma-separated; defaults to the Vite dev origin), not a wildcard -- see
app/main.py's cors_allow_origins() docstring and docs/DECISION_LOG.md
(2026-09-22) for why allow_origins=["*"] was tried and rejected:
SameSite=Strict on the auth cookie blocks cross-SITE requests but not
cross-origin ones on the same site (another port/subdomain), so a
wildcard would let such an origin's JS read back an authenticated
response.

HTTP-level tests build their own isolated FastAPI+CORSMiddleware app
(same construction as app/main.py, given a chosen origin list) rather
than reloading the real `app.main` module -- every other test file also
imports that module, so mutating/reloading it here would risk polluting
their test state.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import cors_allow_origins


def _build_app(allow_origins: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def test_default_is_just_the_vite_dev_origin(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert cors_allow_origins() == ["http://localhost:5173"]


def test_cors_origins_env_var_is_comma_split(monkeypatch):
    """Space after the comma is the natural way a person writes this list
    -- must not survive into the origin string, or it can never match a
    real Origin header again (looks configured, silently rejects everything)."""
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    assert cors_allow_origins() == ["https://a.example", "https://b.example"]


def test_empty_cors_origins_env_var_falls_back_to_default(monkeypatch):
    """Unset and empty-string are both "not configured" -- neither should
    parse into a stray empty-string origin entry."""
    monkeypatch.setenv("CORS_ORIGINS", "")
    assert cors_allow_origins() == ["http://localhost:5173"]


def test_allow_listed_origin_is_reflected_when_credentialed():
    client = TestClient(_build_app(["https://allowed.example", "http://localhost:5173"]))

    resp = client.get(
        "/health",
        headers={"Origin": "https://allowed.example", "Cookie": "access_token=irrelevant-value"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://allowed.example"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_non_allow_listed_origin_gets_no_cors_headers():
    """The actual regression this suite exists to prevent: an origin NOT
    in CORS_ORIGINS must not be reflected, wildcarded, or otherwise
    granted access -- unlike the allow_origins=["*"] design this replaced."""
    client = TestClient(_build_app(["https://allowed.example"]))

    resp = client.get(
        "/health",
        headers={"Origin": "https://totally-unrelated-site.example", "Cookie": "access_token=irrelevant-value"},
    )
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers.keys()}


def test_preflight_rejects_a_non_allow_listed_origin():
    client = TestClient(_build_app(["https://allowed.example"]))

    resp = client.options(
        "/health",
        headers={
            "Origin": "https://totally-unrelated-site.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers.keys()}


def test_preflight_allows_an_allow_listed_origin():
    client = TestClient(_build_app(["https://allowed.example"]))

    resp = client.options(
        "/health",
        headers={"Origin": "https://allowed.example", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://allowed.example"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_no_origin_header_gets_no_cors_headers():
    """A same-origin/non-browser request (no Origin header at all) is CORS
    middleware's no-op path -- it must not add CORS headers unprompted."""
    client = TestClient(_build_app(["https://allowed.example"]))

    resp = client.get("/health")
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers.keys()}


def test_real_app_uses_cors_allow_origins():
    """The actual app wires CORSMiddleware to this same function (not a
    hardcoded/wildcard list) -- a real request against the live app
    reflects the default Vite dev origin when CORS_ORIGINS is unset in
    this environment (docker-compose's api service doesn't set it)."""
    from app.main import app as real_app

    client = TestClient(real_app)
    resp = client.get(
        "/health",
        headers={"Origin": "http://localhost:5173", "Cookie": "access_token=irrelevant-value"},
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"

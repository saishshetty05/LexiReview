"""CORS is intentionally permissive (any origin) so the API isn't limited
to being called through the dev-server's own proxy -- see app/main.py's
CORSMiddleware comment. These tests pin down Starlette's actual
per-request behavior for the "*" + allow_credentials=True combination,
which is NOT a blanket literal "*" on every response: it depends on
whether the request carries a Cookie header.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_no_cookie_gets_the_literal_wildcard():
    """Without a Cookie header there's nothing credentialed to protect --
    Starlette sends the cheaper literal "*", which is correct, not a bug."""
    resp = client.get("/health", headers={"Origin": "https://totally-unrelated-site.example"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "*"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_request_with_a_cookie_gets_the_specific_origin_reflected():
    """This is the case that actually matters: a credentialed request (one
    carrying the auth cookie) must get the exact Origin reflected back, per
    the Fetch spec -- a literal "*" alongside a cookie would make browsers
    refuse to expose the response to credentialed JS."""
    resp = client.get(
        "/health",
        headers={"Origin": "https://totally-unrelated-site.example", "Cookie": "access_token=irrelevant-value"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://totally-unrelated-site.example"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_a_different_origin_with_a_cookie_is_reflected_just_as_readily():
    """Not hardcoded to one allow-listed origin."""
    resp = client.get(
        "/health",
        headers={"Origin": "http://localhost:9999", "Cookie": "access_token=irrelevant-value"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:9999"


def test_preflight_allows_any_origin_and_credentials():
    """Preflight (OPTIONS) always reflects the specific origin regardless
    of a Cookie header -- browsers never send cookies on the preflight
    itself, only on the actual request that follows it."""
    resp = client.options(
        "/documents",
        headers={
            "Origin": "https://totally-unrelated-site.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://totally-unrelated-site.example"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_no_origin_header_gets_no_cors_headers():
    """A same-origin/non-browser request (no Origin header at all) is CORS
    middleware's no-op path -- it must not add CORS headers unprompted."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers.keys()}

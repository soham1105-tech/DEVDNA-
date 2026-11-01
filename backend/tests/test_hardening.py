"""Module 7 — production hardening: request IDs, security headers, readiness."""
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


def test_security_headers_on_api_response(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


def test_request_id_present_and_unique(client):
    ids = set()
    for _ in range(3):
        r = client.get("/api/health")
        rid = r.headers["x-request-id"]
        assert rid and len(rid) <= 32
        ids.add(rid)
    assert len(ids) == 3  # server-generated per request


def test_client_supplied_request_id_is_not_echoed(client):
    r = client.get("/api/health", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert r.headers["x-request-id"] != "<script>alert(1)</script>"
    assert b"<script>" not in r.content


def test_health_ok_is_200(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "ok"


def test_health_degraded_is_503(client):
    """Readiness must fail closed: a broken DB answers 503, not 200."""
    class BrokenSession:
        def execute(self, *_a, **_k):
            raise RuntimeError("disk full")

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        with TestClient(app) as c:
            r = c.get("/api/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["database"] == "error"
    # headers still applied on the error path
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-request-id"]

"""Production hardening middleware: request IDs, access logs, security headers.

Pure-ASGI middlewares (same style as GuardMiddleware) so sync endpoints are
never funnelled through an anyio worker thread.

- RequestIdMiddleware: stamps every response with `X-Request-ID` (generated
  server-side; client-supplied values are ignored, so header injection cannot
  echo attacker-controlled content into responses) and writes one access-log
  line per request with method, path, status and duration.
- SecurityHeadersMiddleware: conservative defaults for an API response —
  nosniff, no framing, no referrer leak, no caching. CORS remains the
  outermost layer so these headers coexist with cross-origin browser calls.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from app.config import settings

log = logging.getLogger("devdna.access")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            path = scope.get("path", "")
            if path not in ("/api/health", "/api/ready"):  # health/readiness probes would flood the log
                method = scope.get("method", "?")
                status_code = status_holder["status"]
                if settings.log_format == "json":
                    log.info(json.dumps({
                        "severity": "ERROR" if status_code >= 500 else ("WARNING" if status_code >= 400 else "INFO"),
                        "message": f"{method} {path} -> {status_code}",
                        "method": method,
                        "path": path,
                        "status": status_code,
                        "duration_ms": round(duration_ms, 1),
                        "request_id": request_id,
                    }))
                else:
                    log.info("%s %s -> %s (%.0f ms) request_id=%s", method,
                             path, status_code, duration_ms, request_id)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in SECURITY_HEADERS
                            if name not in existing]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)

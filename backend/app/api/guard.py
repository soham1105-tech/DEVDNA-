"""Opt-in API guard: token auth and a per-IP request budget.

When `settings.api_token` is set, every `/api` route except `/api/health`
requires an `X-DevDNA-Token` header that matches it — compared with
`hmac.compare_digest`, never `!=` — and the schema endpoints (`/docs`,
`/openapi.json`, `/redoc`) are guarded too, so the API schema does not leak
from a token-protected server. When no token is set the server stays in
local, unauthenticated mode, which must be acknowledged with
`DEVDNA_LOCAL_ONLY=true` or the server refuses to start.

The rate limit is a simple in-memory sliding window per client IP; it counts
every guarded request, including rejected ones, so token guessing is throttled
too. State lives in this process only — it resets on restart and is not
shared across workers.

Implemented as a pure ASGI middleware (deliberately not
`BaseHTTPMiddleware`, which can deadlock sync endpoints under anyio).
Every 401 and 429 rejection is logged with the client IP and path.
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time

from app.config import settings

log = logging.getLogger("devdna.guard")

TOKEN_HEADER = "X-DevDNA-Token"
_WINDOW_S = 60.0
_SCHEMA_PATHS = {"/docs", "/openapi.json", "/redoc"}

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_limit_override: int | None = None


def reset_rate_limit() -> None:
    """Clear all recorded hits (used between tests)."""
    with _lock:
        _hits.clear()


def set_rate_limit(limit: int | None) -> None:
    """Override the per-minute limit; None restores the configured value."""
    global _limit_override
    with _lock:
        _limit_override = limit


def _limit() -> int:
    with _lock:
        return _limit_override if _limit_override is not None else settings.rate_limit_per_minute


def _allow(ip: str) -> bool:
    now = time.monotonic()
    limit = _limit()  # read outside the lock: threading.Lock is not reentrant
    with _lock:
        bucket = [t for t in _hits.get(ip, []) if now - t < _WINDOW_S]
        if len(bucket) >= limit:
            _hits[ip] = bucket
            return False
        bucket.append(now)
        _hits[ip] = bucket
        # keep the map bounded if many IPs appear
        if len(_hits) > 10000:
            for stale in [k for k, v in _hits.items() if not v or now - v[-1] >= _WINDOW_S]:
                del _hits[stale]
        return True


def _client_ip(scope: dict) -> str:
    client = scope.get("client")
    return client[0] if client and client[0] else "unknown"


async def _reject(send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class GuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            schema_path = path in _SCHEMA_PATHS or path.rstrip("/") in _SCHEMA_PATHS
            if path.startswith("/api") or schema_path:
                # the budget covers every guarded route, including health, so
                # token guessing cannot run faster than the limit either
                ip = _client_ip(scope)
                if not _allow(ip):
                    log.warning("429 rate_limited %s %s", ip, path)
                    await _reject(send, 429, "rate_limited",
                                  "Too many requests. Slow down and retry in a minute.")
                    return
                # only auth is exempt for /api/health; schema endpoints are
                # guarded in token mode so the API schema cannot leak
                token = settings.api_token
                auth_exempt = path.startswith("/api") and path.rstrip("/") == "/api/health"
                if token and not auth_exempt:
                    sent = {k.decode("latin-1").lower(): v.decode("latin-1")
                            for k, v in scope.get("headers", [])}
                    supplied = sent.get(TOKEN_HEADER.lower(), "")
                    if not supplied or not hmac.compare_digest(supplied, token):
                        log.warning("401 unauthorized %s %s", ip, path)
                        await _reject(send, 401, "unauthorized",
                                      f"Missing or invalid {TOKEN_HEADER} header.")
                        return
        await self.app(scope, receive, send)

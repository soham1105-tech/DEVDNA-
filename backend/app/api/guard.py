"""Opt-in API guard: token auth and a per-IP request budget.

When `settings.api_token` is set, every `/api` route except `/api/health`
requires an `X-DevDNA-Token` header that matches it. When it is empty the
server stays in local, unauthenticated mode (the startup log warns about it).

The rate limit is a simple in-memory sliding window per client IP; it counts
every `/api` request, including rejected ones, so token guessing is throttled
too. State lives in this process only — it resets on restart and is not
shared across workers.

Implemented as a pure ASGI middleware (deliberately not
`BaseHTTPMiddleware`, which can deadlock sync endpoints under anyio).
"""
from __future__ import annotations

import json
import threading
import time

from app.config import settings

TOKEN_HEADER = "X-DevDNA-Token"
_WINDOW_S = 60.0

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
            if path.startswith("/api"):
                # the budget covers every /api route, including health, so
                # token guessing cannot run faster than the limit either
                if not _allow(_client_ip(scope)):
                    await _reject(send, 429, "rate_limited",
                                  "Too many requests. Slow down and retry in a minute.")
                    return
                # only auth is exempt for /api/health
                token = settings.api_token
                if token and path.rstrip("/") != "/api/health":
                    sent = {k.decode("latin-1").lower(): v.decode("latin-1")
                            for k, v in scope.get("headers", [])}
                    if sent.get(TOKEN_HEADER.lower()) != token:
                        await _reject(send, 401, "unauthorized",
                                      f"Missing or invalid {TOKEN_HEADER} header.")
                        return
        await self.app(scope, receive, send)

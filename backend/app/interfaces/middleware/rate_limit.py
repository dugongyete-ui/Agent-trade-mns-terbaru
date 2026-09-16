"""Lightweight in-memory rate limiting for sensitive endpoints.

Sliding-window counter per (client IP, bucket). No external dependency —
sufficient for a single-process deployment; a distributed deployment should
swap this for a Redis-backed limiter.
"""
import time
import re
import logging
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# (pattern, max_requests, window_seconds, name)
_RULES = [
    (re.compile(r"^/api/v1/auth/login$"), 10, 60, "auth"),
    (re.compile(r"^/api/v1/auth/register$"), 6, 60, "auth"),
    (re.compile(r"^/api/v1/auth/refresh$"), 30, 60, "auth"),
    (re.compile(r"^/api/v1/sessions/[^/]+/chat$"), 30, 60, "chat"),
]

_CLEANUP_INTERVAL = 300  # seconds


class RateLimitMiddleware(BaseHTTPMiddleware):
    """429 when a client exceeds the per-rule sliding-window quota."""

    def __init__(self, app):
        super().__init__(app)
        self._hits: Dict[Tuple[str, str], deque] = defaultdict(deque)
        self._last_cleanup = time.monotonic()

    def _client_ip(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _match(self, path: str) -> Optional[Tuple[str, int, float]]:
        for pattern, limit, window, name in _RULES:
            if pattern.match(path):
                return name, limit, window
        return None

    def _cleanup(self, now: float) -> None:
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        for key in list(self._hits.keys()):
            q = self._hits[key]
            if not q:
                del self._hits[key]

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if request.method in ("OPTIONS",):
            return await call_next(request)

        rule = self._match(path)
        if rule is None:
            return await call_next(request)

        name, limit, window = rule
        now = time.monotonic()
        self._cleanup(now)

        key = (self._client_ip(request), name)
        q = self._hits[key]
        while q and q[0] <= now - window:
            q.popleft()

        if len(q) >= limit:
            retry_after = max(1, int(window - (now - q[0])))
            logger.warning(
                "Rate limit exceeded: ip=%s bucket=%s count=%d",
                key[0], name, len(q),
            )
            return JSONResponse(
                {
                    "code": 429,
                    "msg": "Too many requests — slow down and try again shortly.",
                    "data": None,
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        q.append(now)
        return await call_next(request)

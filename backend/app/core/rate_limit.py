"""In-memory sliding-window rate limits for abuse-prone endpoints (login, sign-up, AI inference).

Limits are per worker process, which is enough to stop password guessing and request floods
from one client; set RATE_LIMIT_ENABLED=false to turn them off (the test suite does).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.config import settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, window_s: float) -> None:
        """Record a hit for `key`; raise 429 if it exceeds `limit` hits per `window_s` seconds."""
        if not settings.rate_limit_enabled:
            return
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_s:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, math.ceil(hits[0] + window_s - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please wait a moment and try again.",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # Behind Nginx, uvicorn --proxy-headers puts the real client address here.
    return request.client.host if request.client else "unknown"


def per_ip(name: str, limit: int, window_s: float) -> Callable[[Request], None]:
    """Dependency: at most `limit` calls per `window_s` seconds from one client address."""

    def dependency(request: Request) -> None:
        limiter.check(f"{name}:ip:{client_ip(request)}", limit, window_s)

    return dependency

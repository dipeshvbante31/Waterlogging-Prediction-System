from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.container import Container


def get_c(request: Request) -> Container:
    return request.app.state.c


def require_admin(request: Request, c: Container = Depends(get_c)):
    tok = c.settings.admin_token
    if not tok:
        if c.settings.app_env == "production":
            raise HTTPException(403, "Administrative endpoints are disabled: set ADMIN_TOKEN.")
        return
    given = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(given.encode(), tok.encode()):
        raise HTTPException(401, "Missing or invalid admin token.")


class RateLimiter:
    """Sliding-window limiter, in-memory and per process (documented limitation)."""
    def __init__(self, name: str):
        self.name = name
        self.hits: dict[str, deque] = defaultdict(deque)

    def __call__(self, request: Request, c: Container = Depends(get_c)):
        limit = c.settings.rate_limit_per_min
        if not limit:
            return
        ip = request.client.host if request.client else "?"
        q, now = self.hits[ip], time.monotonic()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many requests. Please slow down.", headers={"Retry-After": "30"})
        q.append(now)

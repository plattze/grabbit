"""Bearer-token auth with scoped API keys and per-key submit rate limiting."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import Database
from .models import ApiKeyInfo, KeyScope

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

SUBMIT_RATE_LIMIT = 30  # submissions per key per minute
_RATE_WINDOW = 60.0


class RateLimiter:
    def __init__(self, limit: int = SUBMIT_RATE_LIMIT, window: float = _RATE_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, key_id: int) -> bool:
        now = time.monotonic()
        hits = self._hits[key_id]
        while hits and hits[0] < now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


async def ensure_bootstrap_key(db: Database) -> None:
    """First run: create the admin key and print it once."""
    if await db.count_keys() == 0:
        created = await db.create_key("bootstrap-admin", KeyScope.ADMIN)
        # Deliberately bypasses logging config: the operator must see this once.
        print(
            "\n" + "=" * 62 +
            f"\n  Grabbit first run — admin API key (shown ONCE, store it):\n"
            f"\n      {created.token}\n\n" + "=" * 62 + "\n",
            flush=True,
        )


def _db(request: Request) -> Database:
    return request.app.state.db


async def _authenticate(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Database = Depends(_db),
) -> ApiKeyInfo:
    if creds is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    key = await db.verify_key(creds.credentials)
    if key is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    return key


async def require_submit(key: ApiKeyInfo = Depends(_authenticate)) -> ApiKeyInfo:
    return key  # both scopes may submit/list


async def require_admin(key: ApiKeyInfo = Depends(_authenticate)) -> ApiKeyInfo:
    if key.scope != KeyScope.ADMIN:
        raise HTTPException(status_code=403, detail="admin scope required")
    return key

"""In-process event hub fanning job updates out to WebSocket subscribers."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in self._subscribers:
            # Slow consumer: drop; the UI resyncs via GET /api/downloads.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

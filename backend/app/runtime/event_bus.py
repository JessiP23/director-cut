"""In-process async event bus for streaming progress to SSE endpoints."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue):
        subs = self._subscribers.get(run_id, [])
        try:
            subs.remove(q)
        except ValueError:
            pass  # already removed by close()

    async def emit(self, run_id: str, event_type: str, data: dict):
        payload = json.dumps({"type": event_type, **data})
        for q in self._subscribers.get(run_id, []):
            await q.put(payload)

    async def close(self, run_id: str):
        for q in self._subscribers.get(run_id, []):
            await q.put(None)
        self._subscribers.pop(run_id, None)


event_bus = EventBus()

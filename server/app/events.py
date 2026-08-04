"""One SSE channel per canvas so widgets appear the moment a command applies."""

import asyncio
import json
import time
from typing import AsyncIterator

_listeners: dict[str, set[asyncio.Queue]] = {}


def notify(canvas_id: str) -> None:
    payload = json.dumps({"type": "canvas_changed", "at": int(time.time() * 1000)})
    for queue in list(_listeners.get(str(canvas_id), ())):
        try:
            queue.put_nowait(f"data: {payload}\n\n")
        except asyncio.QueueFull:  # a stalled client must not block a command
            pass


async def subscribe(canvas_id: str) -> AsyncIterator[str]:
    canvas_id = str(canvas_id)
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _listeners.setdefault(canvas_id, set()).add(queue)
    try:
        yield ": connected\n\n"
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        listeners = _listeners.get(canvas_id)
        if listeners is not None:
            listeners.discard(queue)
            if not listeners:
                _listeners.pop(canvas_id, None)

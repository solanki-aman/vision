"""Per-canvas SSE, delivered across processes via Postgres LISTEN/NOTIFY.

The earlier version kept listeners in a process-local dict, so `notify()` only reached
browsers attached to *this* process. That was correct while the only writer was the
web process itself. The ambient worker breaks it: a finding it writes has to reach an
open tab in a different process, and an in-memory queue cannot cross that boundary.

So `notify()` now emits `pg_notify('canvas_changed', canvas_id)`, and every process
that serves SSE holds one dedicated connection running `LISTEN`, fanning each
notification out to its own local subscriber queues. The call sites are unchanged, the
payload is a UUID (far under the 8 kB pg_notify limit), and it needs no Redis — we
already run and back up Postgres.
"""

import asyncio
import json
import logging
import time
from typing import AsyncIterator

import asyncpg

from .config import settings

log = logging.getLogger("vision.events")

CHANNEL = "canvas_changed"

# canvas_id -> the queues of tabs watching it, in THIS process only. The cross-process
# story is the LISTEN connection below; this dict is just the local last hop.
_listeners: dict[str, set[asyncio.Queue]] = {}

_listen_conn: asyncpg.Connection | None = None


def _fan_out(canvas_id: str) -> None:
    payload = json.dumps({"type": "canvas_changed", "at": int(time.time() * 1000)})
    for queue in list(_listeners.get(str(canvas_id), ())):
        try:
            queue.put_nowait(f"data: {payload}\n\n")
        except asyncio.QueueFull:  # a stalled client must not block a notification
            pass


def _on_notify(_conn, _pid, _channel, payload: str) -> None:
    _fan_out(payload)


async def start_listener() -> None:
    """Open the process's single LISTEN connection. Called once at startup.

    A dedicated connection rather than one from the pool: LISTEN holds the connection
    for the life of the process, and a pooled connection would be unavailable to
    everyone else for as long as it listened.
    """
    global _listen_conn
    if _listen_conn is not None:
        return
    dsn = settings.database_url.replace("postgres://", "postgresql://", 1)
    _listen_conn = await asyncpg.connect(dsn)
    await _listen_conn.add_listener(CHANNEL, _on_notify)
    log.info("listening for %s notifications", CHANNEL)


async def stop_listener() -> None:
    global _listen_conn
    if _listen_conn is not None:
        try:
            await _listen_conn.remove_listener(CHANNEL, _on_notify)
        finally:
            await _listen_conn.close()
            _listen_conn = None


async def notify_async(canvas_id: str) -> None:
    """Announce a change to every process. Prefer this from async call sites."""
    from .db import pool

    try:
        await pool().execute("SELECT pg_notify($1, $2)", CHANNEL, str(canvas_id))
    except Exception:  # a missed notification is a stale tab, never a failed request
        log.exception("pg_notify failed")
        _fan_out(canvas_id)  # at least reach this process's own tabs


def notify(canvas_id: str) -> None:
    """Fire-and-forget wrapper so existing synchronous call sites keep working.

    Schedules the NOTIFY on the running loop; falls back to a local fan-out if there is
    no loop (e.g. a script), which keeps the old single-process behaviour intact.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(notify_async(canvas_id))
    except RuntimeError:
        _fan_out(canvas_id)


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

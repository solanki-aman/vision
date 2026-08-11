"""The ambient worker: the only process that makes an ambient model call.

Run as `python -m app.worker`. It shares the domain packages with the web service and
holds its own asyncpg pool, but exposes no HTTP surface. Being one process is a
feature: the kill switch is `docker compose stop worker`, and ambient cost is
attributable to one place in any billing view.

The tick is two phases with separate concurrency, deliberately. Refresh is SQL and
runs wide; ambient runs are model calls and must not. `home.due_pins` claims work with
`FOR UPDATE SKIP LOCKED`, the same pattern that already survives a deploy landing
mid-job for document ingest, so two workers never process the same pin.
"""

import asyncio
import logging

from . import ambient, events, finance
from .config import settings
from .db import close_db, init_db, pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vision.worker")


async def tick() -> dict[str, int]:
    """One pass: claim due pins, refresh each, gate, and notice what clears.

    Returns a small tally so the loop can log something legible and a test can assert
    on it.
    """
    pins = await home_due()
    if not pins:
        return {"claimed": 0, "gated": 0, "findings": 0, "failed": 0}

    tally = {"claimed": len(pins), "gated": 0, "findings": 0, "failed": 0}
    sem = asyncio.Semaphore(settings.ambient_concurrency)

    async def one(pin: dict) -> None:
        async with sem:
            try:
                result = await ambient.process_pin(pin, trigger="schedule")
                if result["status"] == "gated":
                    tally["gated"] += 1
                elif result["status"] == "failed":
                    tally["failed"] += 1
                elif result.get("finding"):
                    tally["findings"] += 1
                # A finding (or a freshness bump) is a canvas-relevant change; tell any
                # open tab across every process.
                await events.notify_async(str(pin["canvas_id"]))
            except Exception:  # noqa: BLE001 — one bad pin must not stop the pass
                log.exception("pin %s failed", pin.get("id"))
                tally["failed"] += 1
            finally:
                await release(pin["id"])

    await asyncio.gather(*(one(p) for p in pins))
    return tally


async def home_due():
    from . import home

    return await home.due_pins(limit=64)


async def release(pin_id) -> None:
    from .db import uid

    await pool().execute("UPDATE home.pins SET claimed_at = NULL WHERE id = $1", uid(pin_id))
    # reschedule the pin's next run based on its cadence
    from . import home

    await home.reschedule(pin_id, ok=True)


async def run_forever() -> None:
    await init_db()
    await events.start_listener()
    seeded = await finance.seed()
    if seeded:
        log.info("worker seeded the finance warehouse")

    if not settings.ambient_enabled:
        log.warning(
            "AMBIENT_ENABLED is off — the worker is idle. Set AMBIENT_ENABLED=1 to let "
            "it refresh pins and produce findings (max rung %d).",
            settings.ambient_max_rung,
        )

    log.info(
        "ambient worker ready: tick=%ds, concurrency=%d, max_rung=%d, brief_budget=%d",
        settings.ambient_tick_seconds, settings.ambient_concurrency,
        settings.ambient_max_rung, settings.ambient_brief_budget,
    )
    try:
        while True:
            if settings.ambient_enabled:
                try:
                    tally = await tick()
                    if tally["claimed"]:
                        log.info(
                            "tick: %d claimed, %d gated (no model), %d findings, %d failed",
                            tally["claimed"], tally["gated"], tally["findings"], tally["failed"],
                        )
                except Exception:  # noqa: BLE001
                    log.exception("tick failed")
            await asyncio.sleep(settings.ambient_tick_seconds)
    finally:
        await events.stop_listener()
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        pass

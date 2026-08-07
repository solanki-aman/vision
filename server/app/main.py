"""FastAPI application. Deterministic canvas endpoints plus one streaming agent turn."""

import logging
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from . import events, uistream as ui
from .agent import run_turn
from .commands import apply_change_set, compact, undo_last
from .config import settings
from .db import (
    close_db,
    create_canvas,
    get_canvas_state,
    get_facts,
    get_history,
    get_messages,
    init_db,
    list_canvases,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vision")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("vision server ready on :%s (%s)", settings.port, settings.xai_model)
    yield
    await close_db()


app = FastAPI(title="Vision", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "model": settings.xai_model}


@app.get("/api/canvases")
async def canvases() -> list[dict[str, Any]]:
    return await list_canvases()


@app.post("/api/canvases")
async def new_canvas(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await create_canvas(body.get("title"))


@app.get("/api/canvases/{canvas_id}")
async def canvas(canvas_id: str) -> dict[str, Any]:
    return await get_canvas_state(canvas_id)


@app.get("/api/canvases/{canvas_id}/messages")
async def messages(canvas_id: str) -> list[dict[str, Any]]:
    return await get_messages(canvas_id)


@app.get("/api/canvases/{canvas_id}/facts")
async def facts(canvas_id: str) -> list[dict[str, Any]]:
    """The lineage behind the numbers — one record per retrieved or computed fact."""
    rows = await get_facts(canvas_id)
    return [
        {
            "factId": str(r["id"]),
            "kind": r["kind"],
            "entity": r["entity"],
            "label": r["label"],
            "unit": r["unit"],
            "asOf": r["as_of"],
            "value": r["value"],
            "points": r["points"],
            "tool": r["tool"],
            "query": r["query"],
            "snippet": r["snippet"],
            "sourceUrl": r["source_url"],
            "confidence": r["confidence"],
            "derivedFrom": r["derived_from"],
            "formula": r["formula"],
            "inputs": r["inputs"],
        }
        for r in rows
    ]


def _filename(title: str, fmt: str) -> str:
    """A safe, recognisable download name derived from the canvas title."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "canvas").lower()).strip("-")[:60]
    return f"{slug or 'canvas'}.{fmt}"


@app.get("/api/canvases/{canvas_id}/export")
async def export_canvas(canvas_id: str, format: str = "png") -> Response:
    """Render the whole board headlessly and return it as a download.

    The rendering itself belongs to the shooter service, which already owns the
    only browser in the stack — this just names the file and streams the bytes.
    """
    if format not in ("png", "pdf"):
        raise HTTPException(status_code=400, detail="format must be png or pdf")

    state = await get_canvas_state(canvas_id)
    if not state.get("canvas"):
        raise HTTPException(status_code=404, detail="canvas not found")

    try:
        async with httpx.AsyncClient(timeout=120) as http:
            res = await http.post(
                f"{settings.shooter_url}/export",
                json={"canvasId": canvas_id, "format": format},
            )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"renderer unreachable: {e}") from e

    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"render failed: {res.text[:200]}")

    name = _filename(state["canvas"].get("title", ""), format)
    return Response(
        content=res.content,
        media_type="application/pdf" if format == "pdf" else "image/png",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/canvases/{canvas_id}/events")
async def canvas_events(canvas_id: str) -> StreamingResponse:
    return StreamingResponse(
        events.subscribe(canvas_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "x-accel-buffering": "no"},
    )


@app.post("/api/canvases/{canvas_id}/commands")
async def commands(canvas_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Direct manipulation (GridStack drag/resize) flows through the same command layer."""
    operations = body.get("operations")
    if not isinstance(operations, list):
        raise HTTPException(status_code=400, detail="operations[] required")
    result = await apply_change_set(
        canvas_id, operations, body.get("origin") or "direct_manipulation"
    )
    events.notify(canvas_id)
    return result


@app.post("/api/canvases/{canvas_id}/compact")
async def compact_canvas(canvas_id: str) -> dict[str, Any]:
    moved = await compact(canvas_id)
    events.notify(canvas_id)
    return {"moved": moved}


@app.post("/api/canvases/{canvas_id}/undo")
async def undo(canvas_id: str) -> dict[str, Any]:
    result = await undo_last(canvas_id)
    events.notify(canvas_id)
    return result or {"changeSetId": None, "applied": [], "errors": ["nothing to undo"]}


@app.get("/api/canvases/{canvas_id}/history")
async def history(canvas_id: str) -> list[dict[str, Any]]:
    return await get_history(canvas_id)


@app.post("/api/chat")
async def chat(body: dict[str, Any] = Body(default={})) -> StreamingResponse:
    canvas_id = body.get("canvasId")
    messages = body.get("messages") or []
    if not canvas_id:
        raise HTTPException(status_code=400, detail="canvasId required")

    async def stream():
        async for part in run_turn(canvas_id, messages, lambda: events.notify(canvas_id)):
            yield ui.frame(part)
        yield ui.DONE

    return StreamingResponse(stream(), media_type="text/event-stream", headers=ui.HEADERS)

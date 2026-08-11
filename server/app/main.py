"""FastAPI application. Deterministic canvas endpoints plus one streaming agent turn.

Every canvas route resolves a grant before it touches storage, and an authorization
miss returns 404 rather than 403 — a 403 confirms the canvas exists, which leaks the
id space to anyone willing to enumerate.
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from . import events, ingest, objects, tracing, uistream as ui
from .agent import run_turn
from .auth import (
    OIDC_STATE_COOKIE,
    Principal,
    authorize_redirect,
    clear_session_cookie,
    create_session,
    current_principal,
    destroy_session,
    exchange_code,
    make_render_token,
    metadata,
    purge_expired_sessions,
    require_canvas,
    set_session_cookie,
    unpack_attempt,
    verify_render_token,
)
from .commands import apply_change_set, compact, undo_last
from .config import settings
from .db import (
    close_db,
    create_canvas,
    current_actor,
    get_canvas_state,
    get_facts,
    get_history,
    get_messages,
    init_db,
    list_canvases,
)
from .docstore import (
    backfill_owner_grants,
    can_read_document,
    claim_document,
    delete_document,
    fact_counts,
    get_document,
    grant_canvas,
    list_documents,
    list_grants,
    set_share_scope,
)
from .ingest import IngestError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vision")

# The headless renderer holds a per-canvas capability, not an identity.
RENDER_SUBJECT = "service:shooter"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await objects.ensure_bucket()
    await purge_expired_sessions()
    redaction = tracing.install()
    if settings.auth_enabled and settings.auth_bootstrap_subject:
        granted = await backfill_owner_grants(settings.auth_bootstrap_subject)
        if granted:
            log.info("granted %d pre-existing canvas(es) to the bootstrap subject", granted)
    if not settings.auth_enabled:
        log.warning(
            "AUTHENTICATION IS OFF — every request runs as a single local principal. "
            "Set OIDC_ISSUER and OIDC_CLIENT_ID before exposing this port; documents "
            "are readable by anyone who can reach it."
        )
    log.info(
        "vision server ready on :%s (%s, effort=%s, auth=%s)%s",
        settings.port,
        settings.xai_model,
        settings.xai_reasoning_effort,
        "oidc" if settings.auth_enabled else "disabled",
        f" · tracing → LangSmith project {settings.langsmith_project!r}"
        f" (image redaction: {redaction})"
        if settings.langsmith_tracing
        else "",
    )
    keeper = asyncio.create_task(_maintenance())
    try:
        yield
    finally:
        keeper.cancel()
        await close_db()


async def _maintenance() -> None:
    """The three things that have to happen even when nobody is making a request.

    Erasure, because object storage has no foreign keys and a Postgres cascade
    cannot reach it. Stalled ingest, because upload hands rendering to a background
    task and a deploy in between leaves the row unowned. Session expiry, because
    rows outlive their usefulness and nothing else deletes them.
    """
    while True:
        try:
            await asyncio.sleep(60)
            await ingest.run_deletions()
            await ingest.resume_stalled()
            await purge_expired_sessions()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("maintenance pass failed")


app = FastAPI(title="Vision", lifespan=lifespan)
# Credentialed requests cannot use a wildcard origin, and the session cookie is the
# whole authentication story — so origins are named. In development the client is
# same-origin through the vite proxy and never exercises this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bind_actor(request: Request, call_next):
    """Put the caller in a ContextVar so audit rows name a person, not 'local-user'."""
    token = None
    try:
        principal = await current_principal(request)
        token = current_actor.set(principal.actor)
    except HTTPException:
        pass  # unauthenticated requests are rejected by the route's own dependency
    except Exception:  # noqa: BLE001
        # Naming the actor is for the audit trail, not for serving the request. A
        # session lookup failing here must not turn every route into a 500 — the
        # route's own dependency will reject it properly a moment later.
        log.exception("could not resolve the acting principal")
    try:
        return await call_next(request)
    finally:
        if token is not None:
            current_actor.reset(token)


async def access(
    request: Request, canvas_id: str, *, write: bool = False
) -> tuple[Principal, str]:
    """Resolve the caller and their role on a canvas, or 404.

    Returns the role as well as the principal so callers do not re-derive it — the
    document routes need it, and querying grants twice per request was both wasteful
    and, for the render token below, wrong.

    Honours a short-lived render token: the shooter service loads the SPA headlessly
    to produce exports and has no session, so the export endpoint mints an HMAC token
    scoped to one canvas and valid for two minutes. Narrower than giving the renderer
    a standing credential to everything.
    """
    token = request.query_params.get("rt")
    if token and verify_render_token(token, canvas_id):
        return Principal(subject=RENDER_SUBJECT, name="renderer", role="viewer"), "viewer"
    principal = await current_principal(request)
    role = await require_canvas(canvas_id, principal, write=write)
    return principal, role


# ---- authentication ----------------------------------------------------------------


@app.get("/api/auth/login")
async def login(request: Request, redirect_to: str = "/"):
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="authentication is not configured")
    url, attempt = await authorize_redirect(redirect_to)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OIDC_STATE_COOKIE,
        attempt,
        max_age=600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/api/auth",
    )
    return response


@app.get("/api/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = ""):
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="authentication is not configured")
    attempt = unpack_attempt(request.cookies.get(OIDC_STATE_COOKIE))
    if not code or state != attempt["s"]:
        raise HTTPException(status_code=400, detail="login state mismatch")

    claims = await exchange_code(code, attempt["v"], attempt["n"])
    session_id = await create_session(claims)

    response = RedirectResponse(attempt.get("r") or "/", status_code=302)
    set_session_cookie(response, session_id)
    response.delete_cookie(OIDC_STATE_COOKIE, path="/api/auth")
    return response


@app.post("/api/auth/logout")
async def logout(request: Request):
    session_id = request.cookies.get(settings.session_cookie)
    if session_id:
        await destroy_session(session_id)
    body: dict[str, Any] = {"ok": True}
    if settings.auth_enabled:
        meta = await metadata()
        if meta.get("end_session_endpoint"):
            body["endSessionUrl"] = meta["end_session_endpoint"]
    response = Response(content=ui.json_bytes(body), media_type="application/json")
    clear_session_cookie(response)
    return response


@app.get("/api/auth/me")
async def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {
        "subject": principal.subject,
        "email": principal.email,
        "name": principal.name,
        "role": principal.role,
        "groups": principal.groups,
        "authenticated": settings.auth_enabled,
    }


# ---- canvases -------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "model": settings.xai_model, "auth": "oidc" if settings.auth_enabled else "disabled"}


@app.get("/api/canvases")
async def canvases(principal: Principal = Depends(current_principal)) -> list[dict[str, Any]]:
    return await list_canvases(principal.principals if settings.auth_enabled else None)


@app.post("/api/canvases")
async def new_canvas(
    body: dict[str, Any] = Body(default={}),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    canvas = await create_canvas(body.get("title"))
    await grant_canvas(canvas["id"], f"user:{principal.subject}", "owner", principal.actor)
    return canvas


@app.get("/api/canvases/{canvas_id}")
async def canvas(canvas_id: str, request: Request) -> dict[str, Any]:
    await access(request, canvas_id)
    return await get_canvas_state(canvas_id)


@app.get("/api/canvases/{canvas_id}/messages")
async def messages(canvas_id: str, request: Request) -> list[dict[str, Any]]:
    await access(request, canvas_id)
    return await get_messages(canvas_id)


@app.get("/api/canvases/{canvas_id}/facts")
async def facts(canvas_id: str, request: Request) -> list[dict[str, Any]]:
    """The lineage behind the numbers — one record per retrieved or computed fact."""
    await access(request, canvas_id)
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
async def export_canvas(canvas_id: str, request: Request, format: str = "png") -> Response:
    """Render the whole board headlessly and return it as a download.

    The rendering itself belongs to the shooter service, which already owns the only
    browser in the stack — this just names the file and streams the bytes. The
    renderer has no session, so it is handed a token scoped to this canvas that
    expires in two minutes.
    """
    await access(request, canvas_id)
    if format not in ("png", "pdf"):
        raise HTTPException(status_code=400, detail="format must be png or pdf")

    state = await get_canvas_state(canvas_id)
    if not state.get("canvas"):
        raise HTTPException(status_code=404, detail="canvas not found")

    try:
        async with httpx.AsyncClient(timeout=120) as http:
            res = await http.post(
                f"{settings.shooter_url}/export",
                json={
                    "canvasId": canvas_id,
                    "format": format,
                    "renderToken": make_render_token(canvas_id),
                },
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
async def canvas_events(canvas_id: str, request: Request) -> StreamingResponse:
    await access(request, canvas_id)
    return StreamingResponse(
        events.subscribe(canvas_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "x-accel-buffering": "no"},
    )


@app.post("/api/canvases/{canvas_id}/commands")
async def commands(
    canvas_id: str, request: Request, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Direct manipulation (GridStack drag/resize) flows through the same command layer."""
    await access(request, canvas_id, write=True)
    operations = body.get("operations")
    if not isinstance(operations, list):
        raise HTTPException(status_code=400, detail="operations[] required")
    result = await apply_change_set(
        canvas_id, operations, body.get("origin") or "direct_manipulation"
    )
    events.notify(canvas_id)
    return result


@app.post("/api/canvases/{canvas_id}/compact")
async def compact_canvas(canvas_id: str, request: Request) -> dict[str, Any]:
    await access(request, canvas_id, write=True)
    moved = await compact(canvas_id)
    events.notify(canvas_id)
    return {"moved": moved}


@app.post("/api/canvases/{canvas_id}/undo")
async def undo(canvas_id: str, request: Request) -> dict[str, Any]:
    await access(request, canvas_id, write=True)
    result = await undo_last(canvas_id)
    events.notify(canvas_id)
    return result or {"changeSetId": None, "applied": [], "errors": ["nothing to undo"]}


@app.get("/api/canvases/{canvas_id}/history")
async def history(canvas_id: str, request: Request) -> list[dict[str, Any]]:
    await access(request, canvas_id)
    return await get_history(canvas_id)


# ---- documents ---------------------------------------------------------------------------


def _doc_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "docId": str(row["id"]),
        "filename": row["filename"],
        "mediaType": row["media_type"],
        "pageCount": row.get("page_count"),
        "status": row["status"],
        "error": row.get("error"),
        "shareScope": row.get("share_scope"),
        "digest": row.get("digest"),
    }


@app.post("/api/canvases/{canvas_id}/documents", status_code=201)
async def upload_document(
    canvas_id: str, request: Request, file: UploadFile = File(...)
) -> dict[str, Any]:
    """Store the file and return immediately.

    Contact sheets render in the background; readiness arrives on the canvas event
    stream the client already holds open, so there is no new transport and no polling.
    """
    principal, _ = await access(request, canvas_id, write=True)
    data = await file.read()
    try:
        doc = await ingest.store_upload(
            canvas_id,
            filename=file.filename or "document.pdf",
            media_type=file.content_type or "application/pdf",
            data=data,
            uploaded_by=principal.subject,
        )
    except IngestError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # This request owns the job it just created, so the maintenance loop
    # leaves it alone until the claim goes stale.
    await claim_document(doc["id"])
    asyncio.create_task(ingest.prepare(doc, lambda: events.notify(canvas_id)))
    events.notify(canvas_id)
    return _doc_view(doc)


@app.get("/api/canvases/{canvas_id}/documents")
async def canvas_documents(canvas_id: str, request: Request) -> list[dict[str, Any]]:
    principal, role = await access(request, canvas_id)
    rows = await list_documents(canvas_id)
    return [
        _doc_view(r) for r in rows if can_read_document(r, principal.subject, role)
    ]


async def _readable_document(doc_id: str, request: Request) -> dict[str, Any]:
    doc = await get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    principal, role = await access(request, str(doc["canvas_id"]))
    # The headless renderer holds a capability for this canvas, not an identity, so
    # it can never satisfy an uploader-scoped check. Its token already proves canvas
    # access, and an export is of the canvas the token names.
    if principal.subject == RENDER_SUBJECT:
        return doc
    if not can_read_document(doc, principal.subject, role):
        # Withheld by its uploader. Still 404: the citation UI says "restricted",
        # this endpoint does not confirm anything about the file.
        raise HTTPException(status_code=404, detail="not found")
    return doc


@app.get("/api/documents/{doc_id}")
async def document(doc_id: str, request: Request) -> dict[str, Any]:
    return _doc_view(await _readable_document(doc_id, request))


@app.get("/api/documents/{doc_id}/pages/{page}")
async def document_page(doc_id: str, page: int, request: Request, dpi: int = 0) -> Response:
    """A page image, streamed through the guard.

    Never a presigned URL: those leak via referrer headers, history and screenshots,
    and cannot be revoked once minted.
    """
    doc = await _readable_document(doc_id, request)
    if doc["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"document is {doc['status']}")
    chosen = dpi or settings.doc_read_dpi
    if not 45 <= chosen <= 300:
        raise HTTPException(status_code=400, detail="dpi must be between 45 and 300")
    try:
        data, _ = await ingest.page_image(doc, page, chosen)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/documents/{doc_id}/original")
async def document_original(doc_id: str, request: Request) -> Response:
    doc = await _readable_document(doc_id, request)
    data = await ingest.original_bytes(doc)
    return Response(
        content=data,
        media_type=doc["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{doc["filename"]}"'},
    )


@app.delete("/api/documents/{doc_id}", status_code=204)
async def remove_document(doc_id: str, request: Request) -> Response:
    doc = await get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    await access(request, str(doc["canvas_id"]), write=True)
    await delete_document(
        doc_id, objects.document_prefix(str(doc["canvas_id"]), str(doc["id"]))
    )
    events.notify(str(doc["canvas_id"]))
    return Response(status_code=204)


# ---- sharing -------------------------------------------------------------------------------


@app.get("/api/canvases/{canvas_id}/share-preview")
async def share_preview(canvas_id: str, request: Request) -> dict[str, Any]:
    """What sharing this canvas would disclose.

    Returns how many facts rest on each document so the decision is made with the
    actual exposure in view rather than a filename.
    """
    await access(request, canvas_id, write=True)
    counts = await fact_counts(canvas_id)
    docs = await list_documents(canvas_id)
    return {
        "documents": [
            {
                "docId": str(d["id"]),
                "filename": d["filename"],
                "shareScope": d["share_scope"],
                "factCount": counts.get(str(d["id"]), 0),
            }
            for d in docs
        ],
        "grants": await list_grants(canvas_id),
    }


@app.post("/api/canvases/{canvas_id}/grants")
async def add_grant(
    canvas_id: str, request: Request, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Share a canvas, with an explicit decision about every document it cites.

    Silence is not consent: the request is rejected unless each document is named.
    """
    principal, _ = await access(request, canvas_id, write=True)
    target = body.get("principal")
    role = body.get("role") or "viewer"
    if not target or role not in ("viewer", "editor", "owner"):
        raise HTTPException(status_code=400, detail="principal and a valid role are required")

    decisions = body.get("documents")
    docs = await list_documents(canvas_id)
    if docs:
        if not isinstance(decisions, dict):
            raise HTTPException(
                status_code=400,
                detail="documents{} is required: name each attached document 'canvas' or 'uploader'",
            )
        missing = [d["filename"] for d in docs if d["filename"] not in decisions]
        if missing:
            raise HTTPException(
                status_code=400, detail=f"no sharing decision for: {', '.join(missing)}"
            )
        for d in docs:
            choice = decisions[d["filename"]]
            if choice not in ("canvas", "uploader"):
                raise HTTPException(status_code=400, detail=f"bad choice {choice!r}")
            if choice != d["share_scope"]:
                await set_share_scope(d["id"], choice)

    await grant_canvas(canvas_id, target, role, principal.actor)
    return {"ok": True, "grants": await list_grants(canvas_id)}


# ---- the agent turn --------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(request: Request, body: dict[str, Any] = Body(default={})) -> StreamingResponse:
    canvas_id = body.get("canvasId")
    messages = body.get("messages") or []
    if not canvas_id:
        raise HTTPException(status_code=400, detail="canvasId required")
    principal, _ = await access(request, canvas_id, write=True)

    async def stream():
        async for part in run_turn(
            canvas_id, messages, lambda: events.notify(canvas_id), actor=principal.actor
        ):
            yield ui.frame(part)
        yield ui.DONE

    return StreamingResponse(stream(), media_type="text/event-stream", headers=ui.HEADERS)

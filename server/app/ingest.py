"""Turning an upload into page images, and serving them back from cache.

Three layers meet here and nowhere else: `documents.py` renders (pure), `objects.py`
stores bytes, `docstore.py` records rows. Keeping the seam in one module is what lets
the rendering core stay testable without a database or a bucket.

Rendering is CPU-bound and runs on untrusted input, so it never happens on the event
loop. It also never happens on more than one thread at a time: pdfium is not
thread-safe, and two concurrent calls kill the worker outright with no traceback —
`documents.in_render_thread` is the single thread all of it goes through.
"""

import asyncio
import hashlib
import logging
from typing import Any

from . import objects
from .config import settings
from .db import audit
from .docstore import (
    create_document,
    find_render,
    save_render,
    set_document_failed,
    set_document_ready,
    unique_filename,
)
from .documents import (
    RenderedImage,
    in_render_thread,
    page_count,
    page_size_pt,
    plan_sheets,
    render_page,
    render_sheet,
)

log = logging.getLogger("vision.ingest")


class IngestError(Exception):
    """A rejection the user should see, as opposed to an internal failure."""


async def store_upload(
    canvas_id: str,
    *,
    filename: str,
    media_type: str,
    data: bytes,
    uploaded_by: str,
) -> dict[str, Any]:
    """Validate, store the original, and create a pending row.

    Returns as soon as the bytes are safe and the page count is known; contact
    sheets render afterwards in the background, so the composer is usable
    immediately and learns of readiness over the canvas event stream.
    """
    if len(data) > settings.doc_max_bytes:
        raise IngestError(
            f"file is {len(data) // 1_048_576} MB; the limit is "
            f"{settings.doc_max_bytes // 1_048_576} MB"
        )
    if not data[:5].startswith(b"%PDF-"):
        raise IngestError("only PDF uploads are supported")

    try:
        pages = await in_render_thread(page_count, data)
    except Exception as e:  # noqa: BLE001 — hostile input, not a bug
        raise IngestError(f"could not read the PDF: {e}") from e

    if pages < 1:
        raise IngestError("the PDF has no pages")
    if pages > settings.doc_max_pages:
        raise IngestError(f"{pages} pages exceeds the limit of {settings.doc_max_pages}")

    name = await unique_filename(canvas_id, filename)
    digest = hashlib.sha256(data).hexdigest()

    doc = await create_document(
        canvas_id,
        filename=name,
        media_type=media_type or "application/pdf",
        byte_size=len(data),
        sha256=digest,
        object_key="",
        uploaded_by=uploaded_by,
        page_count=pages,
    )
    key = objects.original_key(str(canvas_id), str(doc["id"]))
    try:
        await objects.put(key, data, doc["media_type"])
        from .db import pool  # local import keeps docstore's query surface narrow

        await pool().execute(
            "UPDATE documents.files SET object_key = $2 WHERE id = $1", doc["id"], key
        )
    except Exception as e:  # noqa: BLE001
        # The row exists but its bytes do not. Say so, rather than leaving a document
        # that sits in `pending` forever and can never be rendered.
        log.exception("failed to store %s", name)
        await set_document_failed(doc["id"], f"could not store the file: {e}")
        raise IngestError("could not store the file") from e
    doc["object_key"] = key
    return doc


async def original_bytes(doc: dict[str, Any]) -> bytes:
    return await objects.get(doc["object_key"])


async def _render_and_cache(
    doc: dict[str, Any],
    *,
    kind: str,
    pages: list[int],
    cols: int,
    dpi: int,
    source: bytes | None = None,
) -> tuple[bytes, RenderedImage]:
    """`source` lets a caller rendering several images from one document fetch the
    original once instead of pulling the whole PDF out of object storage per image."""
    canvas_id, doc_id = str(doc["canvas_id"]), str(doc["id"])
    first, last = pages[0], pages[-1]

    cached = await find_render(doc_id, kind, first, last, dpi)
    if cached:
        data = await objects.get(cached["object_key"])
        meta = RenderedImage(
            kind=kind,
            filename=doc["filename"],
            pages=pages,
            cols=cached["cols"],
            dpi=cached["dpi"],
            width=cached["width"],
            height=cached["height"],
            tokens=cached["tokens"],
        )
        return data, meta

    if source is None:
        source = await original_bytes(doc)
    # Every pdfium call goes through the single render thread — see documents.py.
    if kind == "page":
        data, meta = await in_render_thread(
            render_page, source, doc["filename"], pages[0], dpi
        )
    else:
        data, meta = await in_render_thread(
            render_sheet, source, doc["filename"], pages, cols, dpi
        )

    key = objects.render_key(canvas_id, doc_id, kind, first, last, dpi)
    await objects.put(key, data, "image/png")
    await save_render(
        doc_id,
        kind=kind,
        first_page=first,
        last_page=last,
        cols=meta.cols,
        dpi=dpi,
        width=meta.width,
        height=meta.height,
        tokens=meta.tokens,
        object_key=key,
    )
    return data, meta


async def page_image(doc: dict[str, Any], page: int, dpi: int | None = None) -> tuple[bytes, RenderedImage]:
    """One page. `dpi` defaults to the model's reading resolution; the browser asks
    for a higher one, which caches separately under the same key scheme."""
    return await _render_and_cache(
        doc, kind="page", pages=[page], cols=1, dpi=dpi or settings.doc_page_dpi
    )


async def sheet_image(
    doc: dict[str, Any], pages: list[int], cols: int | None = None, dpi: int | None = None
) -> tuple[bytes, RenderedImage]:
    """A contact sheet for specific pages, cached like any other render."""
    return await _render_and_cache(
        doc,
        kind="sheet",
        pages=pages,
        cols=cols or settings.doc_sheet_cols,
        dpi=dpi or settings.doc_sheet_dpi,
    )


async def sheet_plan_for(
    doc: dict[str, Any], budget: int | None = None, source: bytes | None = None
):
    if source is None:
        source = await original_bytes(doc)
    size = await in_render_thread(page_size_pt, source)
    return plan_sheets(
        doc["page_count"],
        size,
        budget if budget is not None else settings.doc_image_budget,
        cols=settings.doc_sheet_cols,
        dpi=settings.doc_sheet_dpi,
    )


async def contact_sheets(
    doc: dict[str, Any], budget: int | None = None
) -> tuple[list[tuple[bytes, RenderedImage]], Any]:
    """Every sheet the budget allows, plus the plan that produced them.

    The plan carries `truncated_from` when the document did not fit; the caller puts
    that in the label so the model is told rather than silently handed a partial
    document.
    """
    # One fetch of the original for the whole set. Without this a 40-page document
    # pulled its entire PDF out of object storage ten times over.
    source = await original_bytes(doc)
    plan = await sheet_plan_for(doc, budget, source)
    out = []
    for pages in plan.sheets:
        out.append(
            await _render_and_cache(
                doc, kind="sheet", pages=pages, cols=plan.cols, dpi=plan.dpi, source=source
            )
        )
    return out, plan


async def prepare(doc: dict[str, Any], notify=None) -> None:
    """Background task: render the contact sheets, then mark the document ready.

    Failure is recorded on the row rather than raised — this runs detached from the
    upload request, and a document that cannot be rendered should say so in the UI,
    not vanish.
    """
    try:
        sheets, plan = await contact_sheets(doc)
        await set_document_ready(doc["id"], doc["page_count"])
        log.info(
            "ingested %s: %d pages, %d sheets @%ddpi, %d tokens%s",
            doc["filename"],
            doc["page_count"],
            len(sheets),
            plan.dpi,
            plan.tokens,
            f", truncated from p{plan.truncated_from}" if plan.truncated_from else "",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("ingest failed for %s", doc["filename"])
        await set_document_failed(doc["id"], str(e))
    finally:
        if notify is not None:
            notify()


async def resume_stalled() -> int:
    """Re-run ingest for documents whose worker never finished.

    Upload hands rendering to a background task, so a deploy or a crash between the
    upload returning and the sheets landing leaves the row in `pending` with nobody
    working on it. The row is the job; this is what makes it durable.
    """
    from .docstore import abandon_exhausted_documents, claim_stale_documents

    resumed = 0
    for doc in await claim_stale_documents():
        try:
            await prepare(doc)
            resumed += 1
        except Exception:  # noqa: BLE001 — prepare records its own failure
            log.exception("resume failed for %s", doc.get("filename"))
    await abandon_exhausted_documents()
    return resumed


async def run_deletions() -> int:
    """Drain the erasure queue. Postgres cascades never reach object storage, so
    without this the bytes outlive the row that pointed at them."""
    from .docstore import complete_deletion, fail_deletion, pending_deletions

    done = 0
    for job in await pending_deletions():
        try:
            removed = await objects.delete_prefix(job["prefix"])
            await complete_deletion(job["id"], removed)
            done += 1
        except Exception as e:  # noqa: BLE001
            log.exception("erasure failed for %s", job["prefix"])
            await fail_deletion(job["id"], str(e))
    return done


async def record_egress(doc: dict[str, Any], images: list[RenderedImage], actor: str) -> None:
    """Every page that leaves for a model provider is an audit event.

    Not for debugging: so "where did this document go" is answerable about a
    specific file during an incident.
    """
    await audit(
        "document_egress",
        "applied",
        "document",
        doc["id"],
        {
            "filename": doc["filename"],
            "pages": sorted({p for i in images for p in i.pages}),
            "images": len(images),
            "tokens": sum(i.tokens for i in images),
            "model": settings.xai_model,
            "actor": actor,
        },
    )

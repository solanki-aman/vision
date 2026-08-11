"""Queries for documents, renders, access grants and erasure.

Kept out of `db.py` so the document feature reads as one thing, but it shares the
same pool — the DDL lives beside the rest of the schema in `db.py:DOCUMENTS_SCHEMA`.

Two rules are enforced here rather than in callers:

- **Canvas access is always a precondition.** `share_scope` can narrow it to the
  uploader; nothing here can widen it. There is no path where a document grant
  hands out canvas access.
- **Deleting a row queues the bytes for erasure.** Postgres cascades do not reach
  object storage, so a document row never disappears without a deletion job behind it.
"""

import logging
from typing import Any, Sequence

from .db import audit, pool, rows_to_dicts, uid
from .documents import DocumentDigest

log = logging.getLogger("vision.docstore")

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


# ---- grants ----------------------------------------------------------------------


def principals(subject: str, groups: Sequence[str] | None = None) -> list[str]:
    """Every principal string a session can match a grant on."""
    out = [f"user:{subject}"]
    out += [f"group:{g}" for g in (groups or [])]
    return out


async def grant_canvas(
    canvas_id: Any, principal: str, role: str, granted_by: str
) -> None:
    await pool().execute(
        """INSERT INTO canvas.grants (canvas_id, principal, role, granted_by)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (canvas_id, principal)
           DO UPDATE SET role = EXCLUDED.role, granted_by = EXCLUDED.granted_by,
                         granted_at = now()""",
        uid(canvas_id),
        principal,
        role,
        granted_by,
    )
    await audit(
        "grant_canvas", "applied", "canvas", canvas_id, {"principal": principal, "role": role}
    )


async def canvas_role(canvas_id: Any, subject_principals: Sequence[str]) -> str | None:
    """The strongest role any of the caller's principals holds, or None."""
    if not subject_principals:
        return None
    rows = await pool().fetch(
        "SELECT role FROM canvas.grants WHERE canvas_id = $1 AND principal = ANY($2::text[])",
        uid(canvas_id),
        list(subject_principals),
    )
    roles = [r["role"] for r in rows]
    if not roles:
        return None
    return max(roles, key=lambda r: ROLE_RANK.get(r, 0))


async def list_grants(canvas_id: Any) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT principal, role, granted_by, granted_at FROM canvas.grants
           WHERE canvas_id = $1 ORDER BY granted_at""",
        uid(canvas_id),
    )
    return rows_to_dicts(rows)


async def backfill_owner_grants(subject: str) -> int:
    """Give a bootstrap principal ownership of canvases that predate grants.

    Without this, turning authentication on makes every existing canvas
    inaccessible — there is no row saying who owns it, and deny-by-default is
    working correctly when it refuses.
    """
    rows = await pool().fetch(
        """INSERT INTO canvas.grants (canvas_id, principal, role, granted_by)
           SELECT c.id, $1, 'owner', 'bootstrap' FROM canvas.canvases c
           WHERE NOT EXISTS (SELECT 1 FROM canvas.grants g WHERE g.canvas_id = c.id)
           RETURNING canvas_id""",
        f"user:{subject}",
    )
    if rows:
        await audit("backfill_grants", "applied", None, None, {"canvases": len(rows)})
    return len(rows)


# ---- documents -------------------------------------------------------------------


async def unique_filename(canvas_id: Any, filename: str) -> str:
    """The filename is the model's handle for a document, so it must be unambiguous
    within a canvas. Collisions get a suffix rather than an error."""
    rows = await pool().fetch(
        "SELECT filename FROM documents.files WHERE canvas_id = $1", uid(canvas_id)
    )
    taken = {r["filename"] for r in rows}
    if filename not in taken:
        return filename
    stem, _, ext = filename.rpartition(".")
    stem, ext = (stem, f".{ext}") if stem else (filename, "")
    for n in range(2, 1000):
        candidate = f"{stem} ({n}){ext}"
        if candidate not in taken:
            return candidate
    raise ValueError("too many documents with that name")


async def create_document(
    canvas_id: Any,
    *,
    filename: str,
    media_type: str,
    byte_size: int,
    sha256: str,
    object_key: str,
    uploaded_by: str,
    page_count: int | None = None,
) -> dict[str, Any]:
    row = await pool().fetchrow(
        """INSERT INTO documents.files
             (canvas_id, filename, media_type, byte_size, sha256, object_key,
              uploaded_by, page_count, status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending')
           RETURNING id, canvas_id, filename, media_type, byte_size, page_count,
                     status, share_scope, uploaded_by, error, digest""",
        uid(canvas_id),
        filename,
        media_type,
        byte_size,
        sha256,
        object_key,
        uploaded_by,
        page_count,
    )
    await audit(
        "document_upload", "started", "canvas", canvas_id, {"filename": filename, "bytes": byte_size}
    )
    return dict(row)


MAX_INGEST_ATTEMPTS = 4


async def claim_document(doc_id: Any) -> bool:
    """Take ownership of an ingest job. False if someone else already has it."""
    row = await pool().fetchrow(
        """UPDATE documents.files SET claimed_at = now(), attempts = attempts + 1
           WHERE id = $1 AND status = 'pending' RETURNING id""",
        uid(doc_id),
    )
    return row is not None


async def claim_stale_documents(stale_after: int = 300, limit: int = 5) -> list[dict[str, Any]]:
    """Ingest jobs whose worker never finished — a crash, or a deploy mid-render.

    `SKIP LOCKED` keeps two replicas from claiming the same document, and `attempts`
    stops a document that reliably kills the renderer from being retried forever.
    """
    rows = await pool().fetch(
        """UPDATE documents.files SET claimed_at = now(), attempts = attempts + 1
           WHERE id IN (
             SELECT id FROM documents.files
             WHERE status = 'pending'
               AND attempts < $3
               AND (claimed_at IS NULL OR claimed_at < now() - make_interval(secs => $1))
             ORDER BY created_at
             LIMIT $2
             FOR UPDATE SKIP LOCKED
           )
           RETURNING id, canvas_id, filename, media_type, byte_size, page_count,
                     object_key, uploaded_by, share_scope, status, error, digest, attempts""",
        stale_after,
        limit,
        MAX_INGEST_ATTEMPTS,
    )
    if rows:
        log.info("reclaiming %d stalled ingest job(s)", len(rows))
    return rows_to_dicts(rows)


async def abandon_exhausted_documents() -> int:
    """Give up, visibly, on jobs that have burned every attempt."""
    rows = await pool().fetch(
        """UPDATE documents.files
           SET status = 'failed',
               error = COALESCE(error, 'ingest failed repeatedly and was abandoned')
           WHERE status = 'pending' AND attempts >= $1
           RETURNING id""",
        MAX_INGEST_ATTEMPTS,
    )
    return len(rows)


async def set_document_ready(doc_id: Any, page_count: int) -> None:
    await pool().execute(
        "UPDATE documents.files SET status='ready', page_count=$2, error=NULL WHERE id=$1",
        uid(doc_id),
        page_count,
    )
    await audit("document_ingest", "applied", "document", doc_id, {"pages": page_count})


async def set_document_failed(doc_id: Any, error: str) -> None:
    await pool().execute(
        "UPDATE documents.files SET status='failed', error=$2 WHERE id=$1",
        uid(doc_id),
        error[:500],
    )
    await audit("document_ingest", "failed", "document", doc_id, {"error": error[:200]})


async def get_document(doc_id: Any) -> dict[str, Any] | None:
    row = await pool().fetchrow(
        """SELECT id, canvas_id, filename, media_type, byte_size, sha256, page_count,
                  object_key, uploaded_by, share_scope, status, error, digest
           FROM documents.files WHERE id = $1""",
        uid(doc_id),
    )
    return dict(row) if row else None


async def get_document_by_filename(canvas_id: Any, filename: str) -> dict[str, Any] | None:
    row = await pool().fetchrow(
        """SELECT id, canvas_id, filename, media_type, byte_size, sha256, page_count,
                  object_key, uploaded_by, share_scope, status, error, digest
           FROM documents.files WHERE canvas_id = $1 AND filename = $2""",
        uid(canvas_id),
        filename,
    )
    return dict(row) if row else None


async def list_documents(canvas_id: Any, *, ready_only: bool = False) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        f"""SELECT id, canvas_id, filename, media_type, byte_size, page_count,
                   object_key, uploaded_by, share_scope, status, error, digest
            FROM documents.files
            WHERE canvas_id = $1 {"AND status = 'ready'" if ready_only else ""}
            ORDER BY created_at""",
        uid(canvas_id),
    )
    return rows_to_dicts(rows)


async def set_digest(doc_id: Any, digest: DocumentDigest) -> None:
    await pool().execute(
        "UPDATE documents.files SET digest = $2 WHERE id = $1",
        uid(doc_id),
        digest.model_dump(mode="json"),
    )


async def set_share_scope(doc_id: Any, scope: str) -> None:
    if scope not in ("canvas", "uploader"):
        raise ValueError(f"unknown share scope {scope!r}")
    await pool().execute(
        "UPDATE documents.files SET share_scope = $2 WHERE id = $1", uid(doc_id), scope
    )
    await audit("document_share_scope", "applied", "document", doc_id, {"scope": scope})


def can_read_document(doc: dict[str, Any], subject: str, canvas_role_: str | None) -> bool:
    """Canvas access first; `share_scope` may then narrow it to the uploader."""
    if canvas_role_ is None:
        return False
    return doc["share_scope"] == "canvas" or doc["uploaded_by"] == subject


async def delete_document(doc_id: Any, prefix: str) -> None:
    """Drop the row and queue the bytes. Both, or the object outlives the record."""
    await queue_deletion(prefix)
    await pool().execute("DELETE FROM documents.files WHERE id = $1", uid(doc_id))
    await audit("document_delete", "applied", "document", doc_id, {"prefix": prefix})


# ---- render cache ----------------------------------------------------------------


async def find_render(
    file_id: Any, kind: str, first_page: int, last_page: int, dpi: int
) -> dict[str, Any] | None:
    row = await pool().fetchrow(
        """SELECT object_key, width, height, tokens, cols, dpi, first_page, last_page
           FROM documents.renders
           WHERE file_id=$1 AND kind=$2 AND first_page=$3 AND last_page=$4 AND dpi=$5""",
        uid(file_id),
        kind,
        first_page,
        last_page,
        dpi,
    )
    return dict(row) if row else None


async def save_render(
    file_id: Any,
    *,
    kind: str,
    first_page: int,
    last_page: int,
    cols: int,
    dpi: int,
    width: int,
    height: int,
    tokens: int,
    object_key: str,
) -> None:
    await pool().execute(
        """INSERT INTO documents.renders
             (file_id, kind, first_page, last_page, cols, dpi, width, height, tokens, object_key)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           ON CONFLICT (file_id, kind, first_page, last_page, dpi) DO NOTHING""",
        uid(file_id),
        kind,
        first_page,
        last_page,
        cols,
        dpi,
        width,
        height,
        tokens,
        object_key,
    )


# ---- erasure ---------------------------------------------------------------------


async def queue_deletion(prefix: str) -> None:
    await pool().execute("INSERT INTO documents.deletions (prefix) VALUES ($1)", prefix)


async def pending_deletions(limit: int = 50) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT id, prefix, attempts FROM documents.deletions
           WHERE completed_at IS NULL AND attempts < 10
           ORDER BY created_at LIMIT $1""",
        limit,
    )
    return rows_to_dicts(rows)


async def complete_deletion(deletion_id: Any, removed: int) -> None:
    await pool().execute(
        "UPDATE documents.deletions SET completed_at = now() WHERE id = $1", uid(deletion_id)
    )
    await audit("document_erasure", "applied", None, None, {"objects": removed})


async def fail_deletion(deletion_id: Any, error: str) -> None:
    await pool().execute(
        """UPDATE documents.deletions SET attempts = attempts + 1, last_error = $2
           WHERE id = $1""",
        uid(deletion_id),
        error[:500],
    )


# ---- what the model is told ------------------------------------------------------


async def fact_counts(canvas_id: Any) -> dict[str, int]:
    """How many facts on this canvas rest on each document.

    The share dialog shows this so the decision is made with the actual disclosure
    in view rather than a filename.
    """
    rows = await pool().fetch(
        """SELECT source_url, count(*) AS n FROM canvas.facts
           WHERE canvas_id = $1 AND tool = 'document' AND source_url LIKE 'doc://%'
           GROUP BY source_url""",
        uid(canvas_id),
    )
    counts: dict[str, int] = {}
    for r in rows:
        doc_id = r["source_url"].removeprefix("doc://").split("#", 1)[0]
        counts[doc_id] = counts.get(doc_id, 0) + r["n"]
    return counts


async def documents_block(canvas_id: Any) -> str:
    """The '## Attached documents' section of the system message.

    Re-read every turn, exactly as the canvas summary is. The model does not
    remember a document between turns; it is told about one. Delete the document and
    this block disappears, so there is no stale belief to reconcile.
    """
    docs = await list_documents(canvas_id)
    if not docs:
        return ""

    lines: list[str] = []
    for d in docs:
        if d["status"] == "pending":
            lines.append(f"{d['filename']} · still processing, not yet readable")
            continue
        if d["status"] == "failed":
            lines.append(f"{d['filename']} · failed to process ({d.get('error') or 'unknown'})")
            continue
        digest = d.get("digest")
        if digest:
            lines.append(DocumentDigest.model_validate(digest).render())
        else:
            lines.append(f"{d['filename']} · {d['page_count']} pages · not yet summarised")
        lines.append("")

    ready = [d["filename"] for d in docs if d["status"] == "ready"]
    if ready:
        example = ready[0]
        lines.append(
            f'Call view_pages("{example}", "12") to look at a page again, '
            "and cite what you read as [filename p12]."
        )
    return "\n".join(lines).strip()

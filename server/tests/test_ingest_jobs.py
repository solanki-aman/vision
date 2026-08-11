"""Ingest is a resumable job, not a fire-and-forget task.

Upload returns before the contact sheets exist and hands rendering to a background
task. A crash or a deploy in that window used to leave the row in `pending` with
nobody working on it and no way to notice — the document simply never became
readable. The row is the job now, and these pin the state machine that makes it
recoverable. They need a real database and skip cleanly without one.
"""

import pytest

from app.db import close_db, init_db, pool, uid
from app.docstore import (
    MAX_INGEST_ATTEMPTS,
    abandon_exhausted_documents,
    claim_document,
    claim_stale_documents,
    create_document,
    get_document,
    set_document_failed,
    set_document_ready,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def canvas():
    try:
        await init_db()
    except Exception as e:  # noqa: BLE001 — no database in this environment
        pytest.skip(f"database unavailable: {e}")
    row = await pool().fetchrow(
        "INSERT INTO canvas.canvases (title) VALUES ('__ingest_test__') RETURNING id"
    )
    cid = str(row["id"])
    try:
        yield cid
    finally:
        await pool().execute("DELETE FROM canvas.canvases WHERE id = $1", uid(cid))
        await close_db()


async def make_doc(canvas_id: str, name: str = "r.pdf") -> dict:
    return await create_document(
        canvas_id,
        filename=name,
        media_type="application/pdf",
        byte_size=1234,
        sha256="deadbeef",
        object_key=f"{canvas_id}/x/original",
        uploaded_by="tester",
        page_count=5,
    )


async def age_claim(doc_id, seconds: int) -> None:
    await pool().execute(
        "UPDATE documents.files SET claimed_at = now() - make_interval(secs => $2) WHERE id = $1",
        uid(doc_id),
        seconds,
    )


async def test_a_freshly_claimed_job_is_left_alone(canvas):
    """The request that created the document is already rendering it."""
    doc = await make_doc(canvas)
    assert await claim_document(doc["id"]) is True
    stale = await claim_stale_documents(stale_after=300)
    assert doc["id"] not in [d["id"] for d in stale]


async def test_a_stalled_job_is_reclaimed(canvas):
    doc = await make_doc(canvas)
    await claim_document(doc["id"])
    await age_claim(doc["id"], 600)

    stale = await claim_stale_documents(stale_after=300)
    assert doc["id"] in [d["id"] for d in stale]


async def test_an_unclaimed_job_is_picked_up(canvas):
    """A worker that died between INSERT and claim leaves claimed_at NULL."""
    doc = await make_doc(canvas)
    stale = await claim_stale_documents(stale_after=300)
    assert doc["id"] in [d["id"] for d in stale]


async def test_claiming_is_exclusive(canvas):
    """Two passes must not both take the same job — the second sees a fresh claim."""
    doc = await make_doc(canvas)
    first = await claim_stale_documents(stale_after=300)
    second = await claim_stale_documents(stale_after=300)
    assert doc["id"] in [d["id"] for d in first]
    assert doc["id"] not in [d["id"] for d in second]


async def test_a_finished_document_is_never_reclaimed(canvas):
    doc = await make_doc(canvas)
    await set_document_ready(doc["id"], 5)
    await age_claim(doc["id"], 6000)
    assert doc["id"] not in [d["id"] for d in await claim_stale_documents(stale_after=1)]


async def test_a_failed_document_is_never_reclaimed(canvas):
    doc = await make_doc(canvas)
    await set_document_failed(doc["id"], "bad pdf")
    await age_claim(doc["id"], 6000)
    assert doc["id"] not in [d["id"] for d in await claim_stale_documents(stale_after=1)]


async def test_a_document_that_kills_the_renderer_is_eventually_abandoned(canvas):
    """Otherwise a single poisonous PDF is retried forever, every minute, and the
    user sees a spinner that never resolves."""
    doc = await make_doc(canvas)
    for _ in range(MAX_INGEST_ATTEMPTS):
        await age_claim(doc["id"], 6000)
        await claim_stale_documents(stale_after=300)

    assert await claim_stale_documents(stale_after=1) == []
    await abandon_exhausted_documents()

    after = await get_document(doc["id"])
    assert after["status"] == "failed"
    assert "abandoned" in (after["error"] or "")


async def test_attempts_are_counted(canvas):
    doc = await make_doc(canvas)
    await claim_document(doc["id"])
    await age_claim(doc["id"], 600)
    reclaimed = await claim_stale_documents(stale_after=300)
    assert next(d["attempts"] for d in reclaimed if d["id"] == doc["id"]) == 2

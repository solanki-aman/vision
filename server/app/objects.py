"""Object storage for document bytes and their renders.

Originals and page images live here, not in Postgres — the database holds the file
row, the render index and the facts, and never sits on the read path for an image.

**Nothing here is ever handed out as a URL.** Presigned URLs leak through referrer
headers, browser history and screenshots, and cannot be revoked once minted; every
byte is instead streamed through an endpoint that has already resolved the caller's
grant. That is also why the model receives base64 data URLs rather than links: MinIO
is only reachable inside the compose network, so xAI could not fetch a link anyway.

The MinIO SDK is synchronous, so every call is pushed to a worker thread. Object
puts are not hot enough to justify an async S3 stack and its dependency weight.
"""

import asyncio
import io
import logging

from minio import Minio
from minio.error import S3Error

from .config import settings

log = logging.getLogger("vision.objects")

_client: Minio | None = None


def client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def _ensure_bucket_sync() -> None:
    c = client()
    if not c.bucket_exists(settings.minio_bucket):
        c.make_bucket(settings.minio_bucket)


async def ensure_bucket() -> None:
    await asyncio.to_thread(_ensure_bucket_sync)


def _put_sync(key: str, data: bytes, content_type: str) -> None:
    client().put_object(
        settings.minio_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


async def put(key: str, data: bytes, content_type: str) -> str:
    await asyncio.to_thread(_put_sync, key, data, content_type)
    return key


def _get_sync(key: str) -> bytes:
    response = None
    try:
        response = client().get_object(settings.minio_bucket, key)
        return response.read()
    finally:
        if response is not None:
            response.close()
            response.release_conn()


async def get(key: str) -> bytes:
    return await asyncio.to_thread(_get_sync, key)


def _delete_prefix_sync(prefix: str) -> int:
    c = client()
    removed = 0
    for obj in c.list_objects(settings.minio_bucket, prefix=prefix, recursive=True):
        try:
            c.remove_object(settings.minio_bucket, obj.object_name)
            removed += 1
        except S3Error:  # noqa: PERF203 — one bad object must not abort the sweep
            log.exception("failed to remove %s", obj.object_name)
    return removed


async def delete_prefix(prefix: str) -> int:
    """Remove every object under a prefix.

    Postgres `ON DELETE CASCADE` does not reach object storage, so deleting a canvas
    or a document has to call this explicitly or the bytes outlive the record — a
    storage leak and an erasure failure at once.
    """
    return await asyncio.to_thread(_delete_prefix_sync, prefix)


def document_prefix(canvas_id: str, doc_id: str) -> str:
    return f"{canvas_id}/{doc_id}/"


def original_key(canvas_id: str, doc_id: str) -> str:
    return f"{canvas_id}/{doc_id}/original"


def render_key(canvas_id: str, doc_id: str, kind: str, first: int, last: int, dpi: int) -> str:
    span = f"{first}" if first == last else f"{first}-{last}"
    return f"{canvas_id}/{doc_id}/{kind}/{span}@{dpi}.png"

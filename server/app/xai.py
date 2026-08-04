"""The single place that talks to a model provider (plan §7.9)."""

from functools import lru_cache

from xai_sdk import AsyncClient

from .config import settings


@lru_cache(maxsize=1)
def client() -> AsyncClient:
    if not settings.xai_api_key:
        raise RuntimeError("XAI_API_KEY is not set")
    # Agentic turns with server-side tools can run for minutes.
    return AsyncClient(api_key=settings.xai_api_key, timeout=3600)

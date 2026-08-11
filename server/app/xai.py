"""The single place that talks to xAI.

Two clients: ``client()`` is the raw xai-sdk async client (image generation via
Grok Imagine); ``chat_model()`` is the LangChain ``ChatXAI`` the LangGraph agent
drives, with Grok Live Search standing in for the old web/X search tools.
"""

from functools import lru_cache
from typing import Any, Sequence

from langchain_xai import ChatXAI
from xai_sdk import AsyncClient

from .config import settings


@lru_cache(maxsize=1)
def client() -> AsyncClient:
    if not settings.xai_api_key:
        raise RuntimeError("XAI_API_KEY is not set")
    # Agentic turns with server-side tools can run for minutes.
    return AsyncClient(api_key=settings.xai_api_key, timeout=3600)


def chat_model(tools: Sequence[Any]):
    """A tool-bound ChatXAI. Live search is a `web_search` function tool inside
    `tools` (see search.py), not a built-in — the built-in loses its results across
    the multi-tool loop. The API key is read from XAI_API_KEY in the env."""
    if not settings.xai_api_key:
        raise RuntimeError("XAI_API_KEY is not set")
    llm = ChatXAI(
        model=settings.xai_model,
        reasoning_effort=settings.xai_reasoning_effort,
        timeout=3600,
    )
    return llm.bind_tools(tools)

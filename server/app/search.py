"""Live web/X search as a self-contained call, exposed to the agent as a
first-class function tool.

langchain-xai's built-in web_search doesn't survive the multi-tool LangGraph loop
(results are lost on later turns). So search runs here as its own xai-sdk call with
server-side web + X search; the summary and its sources come back to the graph as a
ToolMessage, which persists across turns like any other tool result.
"""

import logging
from typing import Any

from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search

from .config import settings
from .xai import client

log = logging.getLogger("vision.search")

MAX_SOURCES = 8


async def run_search(query: str) -> dict[str, Any]:
    chat = client().chat.create(
        model=settings.xai_model,
        tools=[web_search(), x_search()],
        store_messages=False,
    )
    chat.append(
        user(
            "Search the web and X, then answer with a concise, factual brief: the key "
            "numbers, names and dates that answer the question, each dated. Do not add "
            "commentary. Question: " + query
        )
    )
    response = await chat.sample()
    summary = getattr(response, "content", "") or ""
    sources: list[str] = []
    for c in getattr(response, "citations", None) or []:
        url = c if isinstance(c, str) else getattr(c, "url", "")
        if url and url not in sources:
            sources.append(url)
        if len(sources) >= MAX_SOURCES:
            break
    return {"ok": True, "summary": summary, "sources": sources}

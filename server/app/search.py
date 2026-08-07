"""Live web/X search as a self-contained call, exposed to the agent as a
first-class function tool.

langchain-xai's built-in web_search doesn't survive the multi-tool LangGraph loop
(results are lost on later turns). So search runs here as its own xai-sdk call with
server-side web + X search; the summary and its sources come back to the graph as a
ToolMessage, which persists across turns like any other tool result.
"""

import json
import logging
from typing import Any

from langsmith import traceable
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search

from .config import settings
from .specs import validate_fact
from .xai import client

log = logging.getLogger("vision.search")

MAX_SOURCES = 8

# Extraction, not prose: the search must hand back each figure as a structured fact
# pinned to the source sentence it was read from, so a widget can later bind to it.
EXTRACT_INSTRUCTION = (
    "Search the web and X, then return ONLY a JSON object — no prose, no code fences — shaped:\n"
    '{"brief": "<one short factual paragraph, every figure dated>",\n'
    ' "facts": [\n'
    '   {"entity": "<the subject, e.g. a company or ticker>",\n'
    '    "label": "<what it measures, e.g. FY25 revenue>",\n'
    '    "unit": "<unit or null>", "asOf": "<YYYY-MM-DD or period>",\n'
    '    "value": <number for a single figure, else null>,\n'
    '    "points": [{"x": "<date/category>", "y": <number>}] | null,\n'
    '    "snippet": "<the exact source sentence the number came from>",\n'
    '    "sourceUrl": "<url>", "confidence": "measured"}\n'
    " ]}\n"
    "Every number a reader might chart must appear as a fact with its source snippet. "
    "Use `points` for a series (time series or ranked set) and `value` for a single figure. "
    "Question: "
)


def _extract_json(text: str) -> dict[str, Any]:
    """Recover the JSON object from the model's reply, tolerant of stray fences or prose."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        s = s[nl + 1 :] if nl != -1 else s
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start : end + 1]
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001 — a malformed reply degrades to brief-only, never crashes the turn
        return {}


# Search runs on the raw xai-sdk, outside LangChain, so it would otherwise be a
# blind spot in the trace — and it is usually the slowest step in a turn. The
# decorator is inert when tracing is off.
@traceable(run_type="retriever", name="web_search")
async def run_search(query: str) -> dict[str, Any]:
    chat = client().chat.create(
        model=settings.search_model,
        tools=[web_search(), x_search()],
        store_messages=False,
    )
    chat.append(user(EXTRACT_INSTRUCTION + query))
    response = await chat.sample()
    raw = getattr(response, "content", "") or ""

    data = _extract_json(raw)
    summary = data.get("brief") or raw
    facts: list[dict[str, Any]] = []
    for f in data.get("facts") or []:
        parsed, error = validate_fact(f)
        if parsed:
            facts.append(parsed)
        else:
            log.debug("dropping malformed fact: %s (%s)", f, error)

    sources: list[str] = []
    for c in getattr(response, "citations", None) or []:
        url = c if isinstance(c, str) else getattr(c, "url", "")
        if url and url not in sources:
            sources.append(url)
        if len(sources) >= MAX_SOURCES:
            break
    return {"ok": True, "summary": summary, "facts": facts, "sources": sources}

"""One agent turn, as a LangGraph run bridged to the AI SDK UI message stream.

The LangGraph graph (``graph.py``) owns the ReAct loop; this module rebuilds the
conversation for the graph, streams its events, and translates them into the
typed UI parts the React client speaks. The model never writes storage — every
tool call becomes a change set the command layer validates and applies.
"""

import ast
import datetime as dt
import json
import logging
from typing import Any, AsyncIterator, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from . import uistream as ui
from .commands import normalize_rows
from .config import settings
from .db import audit, get_canvas_summary, rename_canvas_if_untitled, save_messages
from .graph import build_graph
from .prompt import system_prompt
from .tools import TurnFlags, build_tools

log = logging.getLogger("vision.agent")


def last_user_text(messages: Sequence[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            for p in m.get("parts") or []:
                if p.get("type") == "text" and p.get("text"):
                    return p["text"]
    return ""


def _message_text(message: dict[str, Any]) -> str:
    return "".join(
        p.get("text", "")
        for p in message.get("parts") or []
        if p.get("type") == "text"
    ).strip()


def to_lc_messages(messages: Sequence[dict[str, Any]]) -> list[Any]:
    """Replay the conversation as plain turns. Tool traffic from previous turns is
    deliberately not replayed: the canvas summary handed to the model each turn is
    the authoritative record of what those calls produced."""
    out: list[Any] = []
    for m in messages:
        text = _message_text(m)
        if not text:
            continue
        if m.get("role") == "user":
            out.append(HumanMessage(text))
        elif m.get("role") == "assistant":
            out.append(AIMessage(text))
    return out


# ChatXAI on the Responses API emits content as a list of typed blocks — text,
# reasoning (summary_text deltas), web_search_call — with citations as url_citation
# annotations on text blocks. These helpers read that shape and the older plain-string
# Completions shape alike, so the bridge is robust to both.

def _chunk_text(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") or ""
            for b in content
            if isinstance(b, dict) and b.get("type") in ("text", "output_text")
        )
    return ""


def _chunk_reasoning(msg: Any) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "reasoning":
                for s in b.get("summary") or []:
                    if isinstance(s, dict) and s.get("type") == "summary_text":
                        parts.append(s.get("text", "") or "")
        if parts:
            return "".join(parts)
    ak = getattr(msg, "additional_kwargs", None) or {}
    r = ak.get("reasoning_content")
    return r if isinstance(r, str) else ""


def _citations(msg: Any) -> list[str]:
    urls: list[str] = []
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            # Sources the model actually cited, as text annotations.
            if b.get("type") in ("text", "output_text"):
                for a in b.get("annotations") or []:
                    if isinstance(a, dict) and a.get("type") == "url_citation" and a.get("url"):
                        urls.append(a["url"])
            # Sources it consulted, from the web_search call itself.
            elif b.get("type") == "web_search_call":
                for s in (b.get("action") or {}).get("sources") or []:
                    if isinstance(s, dict) and s.get("url"):
                        urls.append(s["url"])
    for attr in ("additional_kwargs", "response_metadata"):
        d = getattr(msg, attr, None) or {}
        found = d.get("citations")
        if isinstance(found, list):
            for item in found:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict) and item.get("url"):
                    urls.append(item["url"])
    return urls


def _tool_output(content: Any) -> Any:
    """ToolNode stringifies dict returns; recover structured output for the rail."""
    if isinstance(content, (dict, list)):
        return content
    if isinstance(content, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                return parse(content)
            except Exception:  # noqa: BLE001
                continue
        return {"result": content}
    return {"result": content}


async def run_turn(
    canvas_id: str,
    messages: list[dict[str, Any]],
    on_change,
) -> AsyncIterator[dict[str, Any]]:
    """Yields UI message stream parts for one user turn."""
    prompt_text = last_user_text(messages)
    if prompt_text:
        await rename_canvas_if_untitled(canvas_id, prompt_text)

    summary = await get_canvas_summary(canvas_id)
    await audit("agent_run", "started", "canvas", canvas_id)

    turn = TurnFlags()
    tools = build_tools(canvas_id, on_change, turn)
    graph = build_graph(tools)
    today = dt.date.today().isoformat()

    lc_messages: list[Any] = [
        SystemMessage(f"{system_prompt()}\n\n## Current canvas\n\n{summary}\n\nToday is {today}.")
    ]
    lc_messages += to_lc_messages(messages)

    message_id = ui.new_id("msg")
    parts: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    pending_inputs: dict[str, dict[str, Any]] = {}  # toolCallId -> {name, input}

    text_id = ui.new_id("txt")
    reasoning_id = ui.new_id("rsn")
    text_open = reasoning_open = False
    text_buf: list[str] = []
    reasoning_buf: list[str] = []
    step_open = False

    config = {"recursion_limit": 2 * settings.max_steps + 1}

    yield ui.start(message_id)

    try:
        yield ui.start_step()
        step_open = True

        async for mode, chunk in graph.astream(
            {"messages": lc_messages}, config=config, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                msg, meta = chunk
                if (meta or {}).get("langgraph_node") != "agent":
                    continue
                delta = _chunk_text(msg)
                if delta:
                    if not text_open:
                        text_open = True
                        yield ui.text_start(text_id)
                    text_buf.append(delta)
                    yield ui.text_delta(text_id, delta)
                thinking = _chunk_reasoning(msg)
                if thinking:
                    if not reasoning_open:
                        reasoning_open = True
                        yield ui.reasoning_start(reasoning_id)
                    reasoning_buf.append(thinking)
                    yield ui.reasoning_delta(reasoning_id, thinking)
                continue

            # mode == "updates": one node finished.
            for node, payload in (chunk or {}).items():
                node_messages = (payload or {}).get("messages") or []

                if node == "agent":
                    ai = node_messages[-1] if node_messages else None
                    had_reasoning = reasoning_open or bool(reasoning_buf)
                    had_text = text_open or bool(text_buf)

                    if reasoning_open:
                        yield ui.reasoning_end(reasoning_id)
                        parts.append({"type": "reasoning", "text": "".join(reasoning_buf)})
                        reasoning_open = False
                        reasoning_buf = []
                        reasoning_id = ui.new_id("rsn")
                    if text_open:
                        yield ui.text_end(text_id)
                        parts.append({"type": "text", "text": "".join(text_buf)})
                        text_open = False
                        text_buf = []
                        text_id = ui.new_id("txt")

                    if ai is not None:
                        # Reasoning / text that arrived only on the final message, not streamed.
                        full_reasoning = _chunk_reasoning(ai)
                        if full_reasoning and not had_reasoning:
                            rid = ui.new_id("rsn")
                            yield ui.reasoning_start(rid)
                            yield ui.reasoning_delta(rid, full_reasoning)
                            yield ui.reasoning_end(rid)
                            parts.append({"type": "reasoning", "text": full_reasoning})
                        full_text = _chunk_text(ai)
                        if full_text and not had_text:
                            tid = ui.new_id("txt")
                            yield ui.text_start(tid)
                            yield ui.text_delta(tid, full_text)
                            yield ui.text_end(tid)
                            parts.append({"type": "text", "text": full_text})

                        for url in _citations(ai):
                            if url in seen_sources:
                                continue
                            seen_sources.add(url)
                            part = ui.source_url(ui.new_id("src"), url)
                            parts.append(part)
                            yield part

                        for call in getattr(ai, "tool_calls", None) or []:
                            call_id = call.get("id")
                            name = call.get("name")
                            args = call.get("args") or {}
                            pending_inputs[call_id] = {"name": name, "input": args}
                            yield ui.tool_input_available(call_id, name, args)

                elif node == "tools":
                    for tm in node_messages:
                        call_id = getattr(tm, "tool_call_id", None)
                        pending = pending_inputs.pop(call_id, {})
                        name = pending.get("name") or getattr(tm, "name", "") or "tool"
                        output = _tool_output(getattr(tm, "content", ""))
                        yield ui.tool_output_available(call_id, output)
                        # Surface the sources a web_search turned up as citations.
                        if isinstance(output, dict):
                            for url in output.get("sources") or []:
                                if not isinstance(url, str) or url in seen_sources:
                                    continue
                                seen_sources.add(url)
                                src = ui.source_url(ui.new_id("src"), url)
                                parts.append(src)
                                yield src
                        parts.append(
                            {
                                "type": f"tool-{name}",
                                "toolCallId": call_id,
                                "state": "output-available",
                                "input": pending.get("input"),
                                "output": output,
                            }
                        )
                    # Close this model↔tool cycle and open the next.
                    yield ui.finish_step()
                    yield ui.start_step()

        if reasoning_open:
            yield ui.reasoning_end(reasoning_id)
            parts.append({"type": "reasoning", "text": "".join(reasoning_buf)})
        if text_open:
            yield ui.text_end(text_id)
            parts.append({"type": "text", "text": "".join(text_buf)})
        if step_open:
            yield ui.finish_step()

        yield ui.finish()

    except Exception as e:  # a failed turn still persists what it built
        log.exception("stream failed")
        await audit("agent_run", "failed", "canvas", canvas_id, {"error": str(e)})
        yield ui.error(str(e))

    finally:
        try:
            history = [
                {"id": m.get("id"), "role": m.get("role"), "parts": m.get("parts")}
                for m in messages
            ]
            if parts:
                history.append({"id": message_id, "role": "assistant", "parts": parts})
            await save_messages(canvas_id, history)
        except Exception:
            log.exception("persist failed")

        # The skill asks for set_layout every building turn; enforce when skipped.
        if turn.mutated and not turn.laid_out:
            try:
                await normalize_rows(canvas_id)
            except Exception:
                log.exception("normalize failed")

        on_change()

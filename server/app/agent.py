"""One agent turn: Grok streams, calls tools, and the canvas fills in.

The model never writes storage. Every tool call becomes a typed change set that
`commands.py` validates and applies, and the result is fed back so the model can
correct itself.
"""

import datetime as dt
import json
import logging
from typing import Any, AsyncIterator, Sequence

from xai_sdk.chat import assistant, system, tool_result, user
from xai_sdk.tools import code_execution, web_search, x_search

from . import uistream as ui
from .commands import normalize_rows
from .config import settings
from .db import audit, get_canvas_summary, rename_canvas_if_untitled, save_messages
from .prompt import SYSTEM_PROMPT
from .tools import TurnFlags, build_tools, execute
from .xai import client

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


def to_model_messages(messages: Sequence[dict[str, Any]]) -> list[Any]:
    """Replay the conversation as plain turns.

    Tool traffic from previous turns is deliberately not replayed: the canvas
    summary handed to the model each turn already lists every widget and its id,
    which is the authoritative record of what those calls produced.
    """
    out = []
    for m in messages:
        text = _message_text(m)
        if not text:
            continue
        if m.get("role") == "user":
            out.append(user(text))
        elif m.get("role") == "assistant":
            out.append(assistant(text))
    return out


def _tool_calls(response: Any) -> list[Any]:
    return list(getattr(response, "tool_calls", None) or [])


def _delta(chunk: Any, attr: str) -> str:
    value = getattr(chunk, attr, "") or ""
    return value if isinstance(value, str) else ""


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
    today = dt.date.today().isoformat()

    chat = client().chat.create(
        model=settings.xai_model,
        tools=[spec.definition() for spec in tools.values()]
        + [web_search(), x_search(), code_execution()],
        store_messages=False,  # conversation state lives in conversation.messages
    )
    chat.append(
        system(
            f"{SYSTEM_PROMPT}\n\n## Current canvas\n\n{summary}\n\nToday is {today}."
        )
    )
    for m in to_model_messages(messages):
        chat.append(m)

    message_id = ui.new_id("msg")
    parts: list[dict[str, Any]] = []  # persisted as the assistant UIMessage
    seen_sources: set[str] = set()

    yield ui.start(message_id)

    try:
        for _ in range(settings.max_steps):
            yield ui.start_step()

            text_id = ui.new_id("txt")
            reasoning_id = ui.new_id("rsn")
            text_open = reasoning_open = False
            text_buf: list[str] = []
            reasoning_buf: list[str] = []
            response = None

            async for response, chunk in chat.stream():
                delta = _delta(chunk, "content")
                if delta:
                    if not text_open:
                        text_open = True
                        yield ui.text_start(text_id)
                    text_buf.append(delta)
                    yield ui.text_delta(text_id, delta)

                thinking = _delta(chunk, "reasoning_content")
                if thinking:
                    if not reasoning_open:
                        reasoning_open = True
                        yield ui.reasoning_start(reasoning_id)
                    reasoning_buf.append(thinking)
                    yield ui.reasoning_delta(reasoning_id, thinking)

            if reasoning_open:
                yield ui.reasoning_end(reasoning_id)
                parts.append({"type": "reasoning", "text": "".join(reasoning_buf)})
            if text_open:
                yield ui.text_end(text_id)
                parts.append({"type": "text", "text": "".join(text_buf)})

            if response is None:
                break

            for citation in getattr(response, "citations", None) or []:
                url = citation if isinstance(citation, str) else getattr(citation, "url", "")
                if not url or url in seen_sources:
                    continue
                seen_sources.add(url)
                part = ui.source_url(ui.new_id("src"), url)
                parts.append(part)
                yield part

            calls = _tool_calls(response)
            pending = [c for c in calls if c.function.name in tools]

            # Server-side tools (web/X search, code execution) already ran inside the
            # response; surface them in the rail without asking for a result.
            for call in calls:
                if call.function.name in tools:
                    continue
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                part = ui.tool_input_available(call.id, call.function.name, args)
                parts.append(
                    {
                        "type": f"tool-{call.function.name}",
                        "toolCallId": call.id,
                        "state": "output-available",
                        "input": args,
                        "output": {"ok": True},
                    }
                )
                yield part
                yield ui.tool_output_available(call.id, {"ok": True})

            if not pending:
                yield ui.finish_step()
                break

            chat.append(response)

            for call in pending:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError as e:
                    args = {}
                    result: dict[str, Any] = {
                        "ok": False,
                        "errors": [f"{name}: arguments were not valid JSON ({e})"],
                    }
                else:
                    yield ui.tool_input_available(call.id, name, args)
                    try:
                        result = await execute(tools[name], args)
                    except Exception as e:  # a failed tool is the model's problem to fix
                        log.exception("tool %s failed", name)
                        result = {"ok": False, "errors": [f"{name} failed: {e}"]}

                yield ui.tool_output_available(call.id, result)
                parts.append(
                    {
                        "type": f"tool-{name}",
                        "toolCallId": call.id,
                        "state": "output-available",
                        "input": args,
                        "output": result,
                    }
                )
                chat.append(tool_result(json.dumps(result)))

            yield ui.finish_step()

        yield ui.finish()

    except Exception as e:
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

        # The prompt asks for set_layout every building turn; enforce when skipped.
        if turn.mutated and not turn.laid_out:
            try:
                await normalize_rows(canvas_id)
            except Exception:
                log.exception("normalize failed")

        on_change()

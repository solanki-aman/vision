"""The AI SDK UI message stream protocol, written by hand.

The React client is `useChat` from @ai-sdk/react v2, which speaks the v1 UI
message stream: SSE frames carrying typed parts, terminated by `[DONE]`.
"""

import json
import uuid
from typing import Any

HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "x-vercel-ai-ui-message-stream": "v1",
    "x-accel-buffering": "no",
}


def frame(part: dict[str, Any]) -> str:
    return f"data: {json.dumps(part, separators=(',', ':'))}\n\n"


DONE = "data: [DONE]\n\n"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def start(message_id: str) -> dict[str, Any]:
    return {"type": "start", "messageId": message_id}


def start_step() -> dict[str, Any]:
    return {"type": "start-step"}


def finish_step() -> dict[str, Any]:
    return {"type": "finish-step"}


def finish() -> dict[str, Any]:
    return {"type": "finish"}


def text_start(part_id: str) -> dict[str, Any]:
    return {"type": "text-start", "id": part_id}


def text_delta(part_id: str, delta: str) -> dict[str, Any]:
    return {"type": "text-delta", "id": part_id, "delta": delta}


def text_end(part_id: str) -> dict[str, Any]:
    return {"type": "text-end", "id": part_id}


def reasoning_start(part_id: str) -> dict[str, Any]:
    return {"type": "reasoning-start", "id": part_id}


def reasoning_delta(part_id: str, delta: str) -> dict[str, Any]:
    return {"type": "reasoning-delta", "id": part_id, "delta": delta}


def reasoning_end(part_id: str) -> dict[str, Any]:
    return {"type": "reasoning-end", "id": part_id}


def tool_input_available(call_id: str, name: str, args: Any) -> dict[str, Any]:
    return {
        "type": "tool-input-available",
        "toolCallId": call_id,
        "toolName": name,
        "input": args,
    }


def tool_output_available(call_id: str, output: Any) -> dict[str, Any]:
    return {"type": "tool-output-available", "toolCallId": call_id, "output": output}


def source_url(source_id: str, url: str, title: str | None = None) -> dict[str, Any]:
    part: dict[str, Any] = {"type": "source-url", "sourceId": source_id, "url": url}
    if title:
        part["title"] = title
    return part


def error(message: str) -> dict[str, Any]:
    return {"type": "error", "errorText": message}

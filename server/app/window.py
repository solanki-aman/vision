"""Keeping page images from piling up inside one agent turn.

A ReAct loop resends the whole message list on every step, so a tool that returns a
page image adds that image to every subsequent request until the turn ends. At
~1,400 tokens a page and up to `AGENT_MAX_STEPS` iterations, a turn that looks at
six pages would carry all six on the last step for no benefit — the model has
already read them.

The window keeps images from the most recent few tool results and rewrites older
ones to a text stub naming the page. Two steps of headroom is enough to
cross-reference the page just opened against the one before it, while the message
list stays bounded no matter how long the loop runs.

Trimming happens at the model call, not in graph state: `agent.py`'s stream bridge
and the LangSmith trace still see the real history.
"""

import logging
from typing import Any, Iterable, Sequence

from langchain_core.messages import ToolMessage

log = logging.getLogger("vision.window")


def _image_blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "image_url"]


def carries_images(message: Any) -> bool:
    return bool(_image_blocks(getattr(message, "content", None)))


def describe(content: Any) -> str:
    """The stub that replaces an evicted image: what it was, and how to get it back."""
    labels = [
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").startswith("[")
    ]
    named = " ".join(labels) if labels else "[page image]"
    return f"{named} — viewed earlier this turn; call view_pages again to look once more"


class DocumentWindow:
    """Materialises images for the last `keep` tool results, stubs the rest."""

    def __init__(self, keep: int = 2) -> None:
        self.keep = max(0, keep)
        self.evicted = 0

    def trim(self, messages: Sequence[Any]) -> list[Any]:
        indices = [i for i, m in enumerate(messages) if carries_images(m)]
        if len(indices) <= self.keep:
            return list(messages)

        stub_at = set(indices[: len(indices) - self.keep]) if self.keep else set(indices)
        out: list[Any] = []
        for i, message in enumerate(messages):
            if i not in stub_at:
                out.append(message)
                continue
            self.evicted += 1
            out.append(
                ToolMessage(
                    content=describe(message.content),
                    tool_call_id=getattr(message, "tool_call_id", "") or "",
                    name=getattr(message, "name", None),
                )
            )
        log.debug("window: %d image results, %d stubbed", len(indices), len(stub_at))
        return out


def summarise_for_ui(content: Any) -> Any:
    """What the browser is told a tool returned.

    Image blocks carry base64 data URLs — hundreds of kilobytes each. Passing them
    through to the UI stream would ship the whole document to the client a second
    time, over SSE, for a rail that only needs to say what happened.
    """
    images = _image_blocks(content)
    if not images:
        return content
    labels = [
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return {"images": len(images), "pages": " ".join(labels).strip()}

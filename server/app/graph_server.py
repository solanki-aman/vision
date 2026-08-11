"""A LangGraph Server-hostable export of the agent graph.

`app/graph.py` compiles a graph whose tools are bound at *build* time, because the
in-process runtime rebuilds it every turn with that turn's canvas and caller. LangGraph
Server hosts **one** long-lived graph and passes per-run context through `config`, so
this module is the config-driven shape: the same two-node ReAct loop, but the tools are
constructed *inside* the nodes from `config["configurable"]` — `canvas_id` and the
caller's `principals` — rather than closed over at build time.

This is what `langgraph.json` points at. Run it locally with:

    cd server && langgraph dev        # in-memory checkpointer + Studio

and deploy the same graph to Self-Hosted Lite or Cloud SaaS. No checkpointer is
compiled in on purpose: the platform injects and manages one, which is the whole reason
to host here rather than wiring AsyncPostgresSaver by hand.

STATUS: foundation. The in-process `/api/chat` path in `main.py` is unchanged and
remains the live runtime. Cutting the browser over to call the LangGraph Server (via
`langgraph_sdk`) is the remaining step — see home/ambient design notes.
"""

from __future__ import annotations

from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from . import events
from .tools import TurnFlags, build_tools
from .xai import chat_model


class ServerState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _context(config: dict[str, Any] | None) -> tuple[str, Sequence[str]]:
    cfg = (config or {}).get("configurable", {})
    canvas_id = cfg.get("canvas_id")
    if not canvas_id:
        raise ValueError("graph run requires configurable.canvas_id")
    principals = cfg.get("principals") or ["user:local-user"]
    return canvas_id, principals


def _tools_for(config: dict[str, Any] | None):
    canvas_id, principals = _context(config)
    # A fresh TurnFlags per run: this graph does not stream mutation flags back the way
    # the in-process bridge does, so it is write-only bookkeeping here.
    return build_tools(canvas_id, lambda: events.notify(canvas_id), TurnFlags(), principals)


def make_graph():
    """The factory `langgraph.json` calls. Returns a compiled, platform-checkpointed graph."""

    async def agent(state: ServerState, config: dict[str, Any]) -> dict[str, Any]:
        tools = _tools_for(config)
        model = chat_model(tools)
        response = await model.ainvoke(state["messages"])
        return {"messages": [response]}

    async def tools_node(state: ServerState, config: dict[str, Any]) -> dict[str, Any]:
        # ToolNode is constructed per run because the tools carry this run's canvas.
        return await ToolNode(_tools_for(config)).ainvoke(state)

    def route(state: ServerState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    graph = StateGraph(ServerState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    # No checkpointer: LangGraph Server provides and manages persistence. For the
    # in-process runtime's own durable interrupts, compile app/graph.py with
    # AsyncPostgresSaver instead — that is a separate concern from hosting.
    return graph.compile()


# LangGraph Server can import either a factory or a compiled graph; expose both.
graph = make_graph()

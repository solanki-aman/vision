"""The LangGraph agent: a two-node ReAct loop that replaces the hand-rolled
``for _ in range(max_steps)`` loop. ``agent`` calls ChatXAI; ``tools`` runs the
LangChain tools; a conditional edge loops until the model stops calling tools.

The graph is intentionally a plain ``StateGraph`` (not ``create_react_agent``)
so ``agent.py`` retains precise control over how the stream maps onto the AI SDK
UI message protocol.
"""

from typing import Annotated, Any, Callable, Sequence, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .xai import chat_model


class GraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build_graph(tools: Sequence[Any], trim: Callable[[Sequence[Any]], list[Any]] | None = None):
    """``trim`` rewrites the message list immediately before the model call.

    It is applied here rather than to graph state so the untrimmed history survives
    for ``agent.py``'s stream bridge and for the LangSmith trace — only the request
    to the model is shortened. See ``window.DocumentWindow``.
    """
    model = chat_model(tools)
    tool_node = ToolNode(tools)

    async def agent(state: GraphState) -> dict[str, Any]:
        messages = trim(state["messages"]) if trim else state["messages"]
        response = await model.ainvoke(messages)
        return {"messages": [response]}

    def route(state: GraphState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    graph = StateGraph(GraphState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()

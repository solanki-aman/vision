"""Page specs, the in-turn image window, and what reaches the browser.

These cover the three places a document can go wrong once it is in the agent loop:
the model asks for pages that do not exist, images accumulate across steps, or a
base64 payload escapes into the UI stream.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.doctools import image_blocks, is_empty
from app.documents import RenderedImage, group_pages, parse_pages
from app.window import DocumentWindow, carries_images, summarise_for_ui


# ---- page specs -------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("12", [12]),
        ("3-6", [3, 4, 5, 6]),
        ("2,9,4", [2, 4, 9]),
        ("1-3,7", [1, 2, 3, 7]),
        ("  5 , 5 ", [5]),
        ("6-4", [4, 5, 6]),
        ("all", list(range(1, 21))),
    ],
)
def test_parse_pages_accepts_the_shapes_a_model_writes(spec, expected):
    assert parse_pages(spec, 20) == expected


def test_parse_pages_reports_the_real_page_count():
    """The model guesses page numbers off a digest, so being told the count is what
    lets it correct itself instead of retrying the same bad call."""
    with pytest.raises(ValueError, match="document has 5 pages; no page 9"):
        parse_pages("9", 5)


@pytest.mark.parametrize("spec", ["", "   ", "abc", "3-x"])
def test_parse_pages_rejects_nonsense(spec):
    with pytest.raises(ValueError):
        parse_pages(spec, 10)


def test_group_pages_never_exceeds_the_measured_sheet_limit():
    groups = group_pages(list(range(1, 12)), per_sheet=8)
    assert all(len(g) <= 4 for g in groups)
    assert [p for g in groups for p in g] == list(range(1, 12))


# ---- the window -------------------------------------------------------------------


def image_result(page: int, call_id: str) -> ToolMessage:
    meta = RenderedImage(
        kind="page", filename="r.pdf", pages=[page], cols=1, dpi=110,
        width=935, height=1210, tokens=1387,
    )
    return ToolMessage(
        content=image_blocks([(b"\x89PNG fake", meta)]),
        tool_call_id=call_id,
        name="view_pages",
    )


def test_window_keeps_only_the_most_recent_image_results():
    messages = [SystemMessage("s"), HumanMessage("q")]
    for i, page in enumerate([11, 12, 13, 14]):
        messages.append(AIMessage(f"looking at {page}"))
        messages.append(image_result(page, f"call_{i}"))

    trimmed = DocumentWindow(keep=2).trim(messages)

    assert len(trimmed) == len(messages)
    live = [m for m in trimmed if carries_images(m)]
    assert len(live) == 2
    # The survivors are the two most recent pages, not any two.
    assert "r.pdf p13" in str(live[0].content)
    assert "r.pdf p14" in str(live[1].content)
    # The rest became text stubs that still name the page they replaced.
    stubs = [m for m in trimmed if isinstance(m, ToolMessage) and not carries_images(m)]
    assert len(stubs) == 2
    assert all("viewed earlier this turn" in m.content for m in stubs)
    assert "r.pdf p11" in stubs[0].content and "r.pdf p12" in stubs[1].content


def test_window_leaves_a_short_turn_untouched():
    messages = [SystemMessage("s"), HumanMessage("q"), image_result(3, "c0")]
    assert DocumentWindow(keep=2).trim(messages) == messages


def test_window_preserves_tool_call_ids_so_the_exchange_stays_valid():
    """A ToolMessage whose tool_call_id no longer matches its AIMessage makes the
    whole request invalid, so eviction has to keep the id even as it drops pixels."""
    messages = [image_result(p, f"call_{p}") for p in (1, 2, 3)]
    trimmed = DocumentWindow(keep=1).trim(messages)
    assert [m.tool_call_id for m in trimmed] == ["call_1", "call_2", "call_3"]


def test_window_with_zero_keep_stubs_everything():
    messages = [image_result(p, f"c{p}") for p in (1, 2)]
    trimmed = DocumentWindow(keep=0).trim(messages)
    assert not any(carries_images(m) for m in trimmed)


# ---- what the browser is told --------------------------------------------------------


def test_image_results_are_summarised_before_reaching_the_ui():
    """Page images are base64 data URLs of a few hundred kilobytes. Passing them to
    the SSE stream would ship the document to the client a second time."""
    meta = RenderedImage(
        kind="page", filename="r.pdf", pages=[12], cols=1, dpi=110,
        width=935, height=1210, tokens=1387,
    )
    content = image_blocks([(b"x" * 5000, meta)])
    summary = summarise_for_ui(content)

    assert summary == {"images": 1, "pages": "[r.pdf p12]"}
    assert "base64" not in str(summary)


def test_plain_tool_output_passes_through_untouched():
    assert summarise_for_ui({"ok": True}) == {"ok": True}
    assert summarise_for_ui("done") == "done"


# ---- fact hygiene ---------------------------------------------------------------------


def test_a_fact_with_no_number_is_recognised_as_empty():
    assert is_empty({"label": "revenue", "value": None, "points": None})
    assert is_empty({"label": "revenue"})
    assert not is_empty({"label": "revenue", "value": 284.1})
    assert not is_empty({"label": "series", "points": [{"x": "Q1", "y": 1.0}]})

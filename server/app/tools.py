"""The LangChain tools handed to the graph. Every one is a proposal: the tool
turns model output into a typed change set and the command layer decides.

Taxonomy: typed create + typed update per element kind, a generic delete, and
surgical chart edits. Handlers never write storage directly — they call
``apply_change_set`` / ``apply_layout`` / ``apply_lanes``.
"""

from typing import Any, Awaitable, Callable, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from .commands import apply_change_set, apply_lanes, apply_layout
from .compute import run_compute
from .db import get_facts_by_ids, record_facts
from .echarts import build_echarts_option
from .imagine import generate_image
from .search import run_search
from .specs import (
    CHART_TYPES,
    Annotation,
    ChartSpec,
    HeroSpec,
    KpiSpec,
    LabelSpec,
    NarrativeSpec,
    Provenance,
    Series,
    StatementSpec,
    StyleSpec,
    TableSpec,
)

HEIGHT_ROWS = {"label": 1, "kpi": 4, "short": 6, "standard": 8, "tall": 11}


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Size(ToolInput):
    span: int = Field(ge=2, le=12, description="Width in columns of a 12-column row.")
    height: Literal["label", "kpi", "short", "standard", "tall"] = Field(
        description="label=40px heading, kpi=175px tile, short=255px, standard=320px, tall=440px."
    )


SIZE_FIELD = Field(
    default=None,
    description="Omit only when the default size is genuinely right; prefer set_layout at the end.",
)
TITLE_FIELD = Field(description="A title that states the finding, not the topic.")
WIDGET_ID_FIELD = Field(description="The widget's id, from the canvas summary.")


def _to_size(size: dict[str, Any] | None) -> dict[str, int] | None:
    if not size:
        return None
    return {"w": size["span"], "h": HEIGHT_ROWS[size["height"]]}


# ---- create inputs (one per element kind) ---------------------------------------

class Binding(ToolInput):
    path: str = Field(
        description=(
            "Dotted path to the value this fact fills: 'value', 'comparison.baseline', "
            "'series.0' (a whole series from a series-fact), 'sparkline', 'lines.3.value', "
            "'rows.2.revenue'."
        )
    )
    factId: str = Field(description="A factId returned by web_search.")


BINDINGS_FIELD = Field(
    default=None,
    description=(
        "Bind every measured number to a fact from web_search — one entry per scalar value, or "
        "one per series (bind 'series.N' to a series-fact and its data is filled for you). "
        "Required whenever provenance.confidence is 'measured': the command layer fills the value "
        "from the fact and rejects any measured number left unbound. Never hand-type a searched number."
    ),
)


class ChartInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: ChartSpec
    bindings: list[Binding] | None = BINDINGS_FIELD


class KpiInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: KpiSpec
    bindings: list[Binding] | None = BINDINGS_FIELD


class TableInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: TableSpec
    bindings: list[Binding] | None = BINDINGS_FIELD


class NarrativeInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: NarrativeSpec


class StatementInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: StatementSpec
    bindings: list[Binding] | None = BINDINGS_FIELD


class HeroInput(ToolInput):
    title: str
    spec: HeroSpec
    size: Size | None = SIZE_FIELD
    bindings: list[Binding] | None = BINDINGS_FIELD


class LabelInput(ToolInput):
    title: str
    spec: LabelSpec
    size: Size | None = SIZE_FIELD


class ImageInput(ToolInput):
    title: str
    prompt: str = Field(description="Detailed visual prompt. Describe style, composition, palette.")
    quality: bool | None = Field(default=None, description="Slower, higher fidelity.")
    size: Size | None = SIZE_FIELD


# ---- update inputs (typed, per element kind) ------------------------------------

class UpdateChartInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str | None = None
    spec: ChartSpec = Field(description="Full replacement chart spec — include every field to keep.")
    bindings: list[Binding] | None = BINDINGS_FIELD


class UpdateKpiInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str | None = None
    spec: KpiSpec
    bindings: list[Binding] | None = BINDINGS_FIELD


class UpdateTableInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str | None = None
    spec: TableSpec
    bindings: list[Binding] | None = BINDINGS_FIELD


class UpdateNarrativeInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str | None = None
    spec: NarrativeSpec


class UpdateHeroInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str | None = None
    spec: HeroSpec


class UpdateLabelInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str | None = None
    spec: LabelSpec


class UpdateStatementInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str | None = None
    spec: StatementSpec
    bindings: list[Binding] | None = BINDINGS_FIELD


# ---- surgical / generic inputs --------------------------------------------------

class WebSearchInput(ToolInput):
    query: str = Field(
        description="A focused search query for current, real or checkable facts — "
        "numbers, dates, prices, rankings, events."
    )


class ComputeVar(ToolInput):
    name: str = Field(
        description="A short, self-explanatory variable name the code uses, e.g. 'q1_rev' or "
        "'prior_q_rev'. It is shown to the reader in the drill-down, so make it meaningful — not 'x'."
    )
    factId: str = Field(
        description="The fact whose value fills this variable — its number for a scalar fact, "
        "its list of y-values for a series fact."
    )


class ComputeInput(ToolInput):
    label: str = Field(description="What the computed number measures, e.g. 'QoQ revenue growth'.")
    unit: str | None = Field(default=None, description="Unit of the result, e.g. '%', 'USD B'.")
    entity: str | None = Field(default=None, description="The subject, for the colour thread.")
    kind: Literal["scalar", "series"] = Field(
        default="scalar", description="scalar for one number, series for a computed vector."
    )
    inputs: list[ComputeVar] = Field(
        description="The facts to compute from, each bound to a variable name the code uses."
    )
    code: str = Field(
        description=(
            "Python that computes the answer and assigns it to `result`. The code is shown to the "
            "reader AS the explanation of the number, so write it to be read: a short comment on each "
            "step, meaningful names, intermediates spelled out. Example:\n"
            "# Q1 FY27 revenue vs the prior quarter, as a percent change\n"
            "growth = (q1_rev - q4_rev) / q4_rev * 100\n"
            "result = round(growth, 1)\n"
            "For a series, result must be a list of numbers aligned to a series input's points. Only "
            "arithmetic and math.* are available."
        )
    )


class RetitleInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    title: str = Field(description="The new title — a sentence that states the finding.")


class DeleteInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD


class AddChartSeriesInput(ToolInput):
    widgetId: str = Field(description="The chart widget's id, from the canvas summary.")
    series: list[Series] = Field(
        min_length=1,
        description=(
            "One entry per line/bar/slice to append. Each series' data must be aligned to the "
            "chart's existing xAxis categories — same length, same order. Names already on the "
            "chart are ignored."
        ),
    )
    factIds: list[str] | None = Field(
        default=None,
        description=(
            "Optional, aligned to `series`: the series-fact id backing each appended series. When "
            "given, the command layer fills that series' data from the fact and records provenance."
        ),
    )


class RemoveChartSeriesInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    names: list[str] = Field(
        min_length=1, description="Series names to drop, as they appear in the current chart."
    )


class SetChartTypeInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    chartType: CHART_TYPES = Field(description="The new chart form. Data and axes are preserved.")


class SetChartAnnotationsInput(ToolInput):
    widgetId: str = WIDGET_ID_FIELD
    annotations: list[Annotation] = Field(
        max_length=5, description="The full set of on-plot marks; replaces any existing annotations."
    )


# ---- layout / style inputs ------------------------------------------------------

class LayoutItem(ToolInput):
    widgetId: str
    span: int = Field(ge=2, le=12, description="Relative width. Spans in a row should sum to 12.")


class LayoutRow(ToolInput):
    height: Literal["kpi", "short", "standard", "tall"] = Field(
        description="kpi for a metric strip, standard for most chart rows, tall for dense forms."
    )
    items: list[LayoutItem] = Field(
        min_length=1, max_length=4, description="At most four cards per row, in left-to-right order."
    )


class LayoutInput(ToolInput):
    rows: list[LayoutRow] = Field(min_length=1, max_length=12)


class LaneItem(ToolInput):
    widgetId: str
    height: Literal["label", "kpi", "short", "standard", "tall"]


class Lane(ToolInput):
    span: int = Field(ge=2, le=12, description="Relative width; spans across lanes should sum to 12.")
    items: list[LaneItem] = Field(
        min_length=1, max_length=8, description="Cards stacked top to bottom in this lane."
    )


class LanesInput(ToolInput):
    lanes: list[Lane] = Field(min_length=2, max_length=6)


class TurnFlags:
    def __init__(self) -> None:
        self.mutated = False
        self.laid_out = False


def build_tools(
    canvas_id: str, on_change: Callable[[], None], turn: TurnFlags
) -> list[StructuredTool]:
    async def place(op: dict[str, Any]) -> dict[str, Any]:
        if op["kind"] == "add_widget":
            turn.mutated = True
        result = await apply_change_set(canvas_id, [op], "agent")
        on_change()
        if result["errors"]:
            return {"ok": False, "errors": result["errors"]}
        applied = result["applied"]
        return {"ok": True, "widgetId": applied[0]["widgetId"] if applied else None}

    # ---- create -----------------------------------------------------------------
    def adder(widget_kind: str):
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            return await place(
                {
                    "kind": "add_widget",
                    "widgetKind": widget_kind,
                    "title": args["title"],
                    "spec": args["spec"],
                    "provenance": args.get("provenance"),
                    "bindings": args.get("bindings"),
                    "size": _to_size(args.get("size")),
                }
            )

        return run

    async def run_create_chart(args: dict[str, Any]) -> dict[str, Any]:
        # Compile the spec through the echarts tool first — a chart that cannot
        # render is rejected before it ever reaches the canvas.
        try:
            build_echarts_option.invoke({"spec": args["spec"]})
        except Exception as e:  # noqa: BLE001 — surface the render failure to the model
            return {"ok": False, "errors": [f"chart spec does not render: {e}"]}
        return await place(
            {
                "kind": "add_widget",
                "widgetKind": "chart",
                "title": args["title"],
                "spec": args["spec"],
                "provenance": args.get("provenance"),
                "bindings": args.get("bindings"),
                "size": _to_size(args.get("size")),
            }
        )

    async def run_create_image(args: dict[str, Any]) -> dict[str, Any]:
        url = await generate_image(args["prompt"], bool(args.get("quality")))
        return await place(
            {
                "kind": "add_widget",
                "widgetKind": "image",
                "title": args["title"],
                "spec": {"url": url, "prompt": args["prompt"]},
                "provenance": {"source": "Grok Imagine", "confidence": "illustrative"},
                "size": _to_size(args.get("size")),
            }
        )

    # ---- update -----------------------------------------------------------------
    async def run_update(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {
                "kind": "update_widget",
                "widgetId": args["widgetId"],
                "title": args.get("title"),
                "spec": args.get("spec"),
                "bindings": args.get("bindings"),
            }
        )

    async def run_update_chart(args: dict[str, Any]) -> dict[str, Any]:
        try:
            build_echarts_option.invoke({"spec": args["spec"]})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "errors": [f"chart spec does not render: {e}"]}
        return await run_update(args)

    async def run_retitle(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {"kind": "update_widget", "widgetId": args["widgetId"], "title": args["title"], "spec": None}
        )

    async def run_delete(args: dict[str, Any]) -> dict[str, Any]:
        return await place({"kind": "remove_widget", "widgetId": args["widgetId"]})

    # ---- surgical chart edits ---------------------------------------------------
    async def run_add_chart_series(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {
                "kind": "add_chart_series",
                "widgetId": args["widgetId"],
                "series": args["series"],
                "factIds": args.get("factIds"),
            }
        )

    async def run_remove_chart_series(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {"kind": "remove_chart_series", "widgetId": args["widgetId"], "names": args["names"]}
        )

    async def run_set_chart_type(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {"kind": "set_chart_type", "widgetId": args["widgetId"], "chartType": args["chartType"]}
        )

    async def run_set_chart_annotations(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {
                "kind": "set_chart_annotations",
                "widgetId": args["widgetId"],
                "annotations": args["annotations"],
            }
        )

    # ---- canvas-level -----------------------------------------------------------
    async def run_set_style(args: dict[str, Any]) -> dict[str, Any]:
        result = await apply_change_set(canvas_id, [{"kind": "set_style", "style": args}], "agent")
        on_change()
        if result["errors"]:
            return {"ok": False, "errors": result["errors"]}
        return {"ok": True, "applied": args["name"]}

    async def run_set_layout(args: dict[str, Any]) -> dict[str, Any]:
        turn.laid_out = True
        result = await apply_layout(canvas_id, args["rows"])
        on_change()
        return result

    async def run_set_lanes(args: dict[str, Any]) -> dict[str, Any]:
        turn.laid_out = True
        result = await apply_lanes(canvas_id, args["lanes"])
        on_change()
        return result

    async def run_web_search(args: dict[str, Any]) -> dict[str, Any]:
        result = await run_search(args["query"])
        stored = (
            await record_facts(canvas_id, result.get("facts") or [], tool="web_search", query=args["query"])
            if result.get("facts")
            else []
        )
        return {
            "ok": result.get("ok", True),
            "summary": result.get("summary", ""),
            "sources": result.get("sources", []),
            "facts": [
                {
                    "factId": s["factId"],
                    "entity": s.get("entity"),
                    "label": s.get("label"),
                    "value": s.get("value"),
                    "unit": s.get("unit"),
                    "asOf": s.get("as_of"),
                    "source": s.get("source_url"),
                }
                for s in stored
            ],
        }

    async def run_code_execution(args: dict[str, Any]) -> dict[str, Any]:
        specs = args.get("inputs") or []
        ids = [s["factId"] for s in specs]
        facts = await get_facts_by_ids(canvas_id, ids)
        env: dict[str, Any] = {}
        series_x: list[str] | None = None
        for s in specs:
            f = facts.get(str(s["factId"]))
            if f is None:
                return {"ok": False, "errors": [f'compute input fact {s["factId"]} not found']}
            if f["kind"] == "series":
                pts = f["points"] or []
                env[s["name"]] = [p.get("y") for p in pts]
                if series_x is None:
                    series_x = [p.get("x") for p in pts]
            else:
                env[s["name"]] = f["value"]
        try:
            result = run_compute(args["code"], env)
        except ValueError as e:
            return {"ok": False, "errors": [str(e)]}

        kind = args.get("kind") or "scalar"
        fact: dict[str, Any] = {
            "kind": kind,
            "entity": args.get("entity"),
            "label": args["label"],
            "unit": args.get("unit"),
            "confidence": "estimated",
            "derivedFrom": ids,
            "formula": args["code"],
            # The variable→fact mapping, so the drill-down can name each input and its source.
            "inputs": [{"name": s["name"], "factId": s["factId"]} for s in specs],
        }
        if kind == "series":
            if not isinstance(result, (list, tuple)):
                return {"ok": False, "errors": ["a series compute must return a list of numbers"]}
            xs = series_x or [str(i) for i in range(len(result))]
            fact["points"] = [
                {"x": xs[i] if i < len(xs) else str(i), "y": v} for i, v in enumerate(result)
            ]
        else:
            if not isinstance(result, (int, float)) or isinstance(result, bool):
                return {"ok": False, "errors": ["a scalar compute must return a single number"]}
            fact["value"] = float(result)

        stored = await record_facts(canvas_id, [fact], tool="code_execution", query=None)
        on_change()
        return {
            "ok": True,
            "factId": stored[0]["factId"],
            "label": args["label"],
            "value": fact.get("value"),
            "unit": args.get("unit"),
            "derivedFrom": ids,
        }

    def tool(
        name: str,
        description: str,
        model: type[BaseModel],
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> StructuredTool:
        async def coroutine(**kwargs: Any) -> dict[str, Any]:
            # Re-validate against the model, then hand the command layer plain JSON.
            args = model.model_validate(kwargs).model_dump(mode="json", by_alias=True, exclude_none=True)
            return await handler(args)

        return StructuredTool.from_function(
            coroutine=coroutine, name=name, description=description, args_schema=model
        )

    return [
        tool(
            "web_search",
            "Search the live web and X for current, real or checkable facts — numbers, "
            "dates, prices, rankings, recent events. Call this FIRST, before building, "
            "whenever a question touches anything you should not guess. Returns a factual "
            "brief, its source URLs, and structured facts each with a factId — bind every "
            "measured number you build to one of those factIds instead of typing it.",
            WebSearchInput,
            run_web_search,
        ),
        tool(
            "code_execution",
            "Run Python over facts to compute a derived number — a growth rate, ratio, sum, or "
            "reshaped series — instead of doing the arithmetic yourself. Name each input fact, write "
            "code that assigns `result`, and it returns a new factId you bind like any other. The "
            "computed fact stores its formula and the facts it came from, so its provenance shows the "
            "whole derivation. Prefer this over mental math for any number you present.",
            ComputeInput,
            run_code_execution,
        ),
        tool(
            "set_style",
            "Give this canvas its own visual identity before building — an accent drawn from the "
            "subject, a typographic voice, a paper tint, a card treatment. Call it once, first.",
            StyleSpec,
            run_set_style,
        ),
        tool(
            "create_chart",
            "Place a chart on the canvas. Your primary tool — reach for it first.",
            ChartInput,
            run_create_chart,
        ),
        tool(
            "create_kpi",
            "Place a single decisive number with an optional comparison and sparkline. Use for "
            "headline metrics instead of stating them in text.",
            KpiInput,
            adder("kpi"),
        ),
        tool(
            "create_table",
            "Place an exact-values table. Use when precision matters more than shape.",
            TableInput,
            adder("table"),
        ),
        tool(
            "create_narrative",
            "Place a short annotation card — the 'so what', a caveat, or a recommendation. "
            "This is where prose belongs.",
            NarrativeInput,
            adder("narrative"),
        ),
        tool(
            "create_hero",
            "Open the canvas with its thesis in display type — one huge number or claim with a "
            "supporting line. Place it first, full width, height 'kpi'. One per canvas.",
            HeroInput,
            adder("hero"),
        ),
        tool(
            "create_label",
            "Place a section heading — a bare title band with no card chrome. Use these to name "
            "the stages of a flow, or to group a canvas into labelled bands. Height 'label'.",
            LabelInput,
            adder("label"),
        ),
        tool(
            "create_statement",
            "Place a financial statement ledger — ordered lines with +, - and = markers, subtotals "
            "and a highlighted total. Use for a P&L build, a cash bridge, or any figure built up "
            "from components. This is a calculation, not a table.",
            StatementInput,
            adder("statement"),
        ),
        tool(
            "create_image",
            "Generate an image with Grok Imagine and place it on the canvas. Use for concepts, "
            "moods, diagrams-as-art, or anything better shown than plotted.",
            ImageInput,
            run_create_image,
        ),
        tool(
            "update_chart",
            "Replace a chart's whole spec by id (full replacement — include every field to keep). "
            "For adding/removing series, changing type, or annotations, prefer the surgical tools.",
            UpdateChartInput,
            run_update_chart,
        ),
        tool("update_kpi", "Replace a KPI widget's spec by id.", UpdateKpiInput, run_update),
        tool("update_table", "Replace a table widget's spec by id.", UpdateTableInput, run_update),
        tool(
            "update_narrative",
            "Replace a narrative widget's spec by id — rewrite the takeaway.",
            UpdateNarrativeInput,
            run_update,
        ),
        tool("update_hero", "Replace a hero widget's spec by id.", UpdateHeroInput, run_update),
        tool("update_label", "Replace a label widget's spec by id.", UpdateLabelInput, run_update),
        tool(
            "update_statement",
            "Replace a statement widget's spec by id.",
            UpdateStatementInput,
            run_update,
        ),
        tool(
            "retitle_widget",
            "Change any widget's title by id, leaving its spec untouched. Use for 'rename that'.",
            RetitleInput,
            run_retitle,
        ),
        tool(
            "delete_widget",
            "Take a widget off the canvas by id. Reversible via undo.",
            DeleteInput,
            run_delete,
        ),
        tool(
            "add_chart_series",
            "Append one or more series to an existing chart, preserving the other series, axes and "
            "title. Use for 'add X to the chart', 'compare with Y', 'overlay Z'. Each series' data "
            "must align to the chart's existing xAxis categories. Duplicate names are ignored.",
            AddChartSeriesInput,
            run_add_chart_series,
        ),
        tool(
            "remove_chart_series",
            "Drop one or more series from an existing chart by name, keeping everything else intact.",
            RemoveChartSeriesInput,
            run_remove_chart_series,
        ),
        tool(
            "set_chart_type",
            "Change an existing chart's form (e.g. line to bar) by id, preserving its data and axes.",
            SetChartTypeInput,
            run_set_chart_type,
        ),
        tool(
            "set_chart_annotations",
            "Replace an existing chart's on-plot marks (reference lines, moments, eras, callouts).",
            SetChartAnnotationsInput,
            run_set_chart_annotations,
        ),
        tool(
            "set_layout",
            "Arrange the whole canvas as a dashboard. Pass an ordered list of rows; every card in a "
            "row shares one height and the spans are fitted to 12 columns. Call once, last.",
            LayoutInput,
            run_set_layout,
        ),
        tool(
            "set_lanes",
            "Arrange the canvas as side-by-side vertical columns instead of rows — the shape a flow "
            "or pipeline needs. One lane per stage. Use instead of set_layout, not as well.",
            LanesInput,
            run_set_lanes,
        ),
    ]

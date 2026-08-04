"""Client-side tools handed to Grok. Every one of them is a proposal: the tool
turns model output into a typed change set and the command layer decides."""

import copy
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from xai_sdk.chat import tool as xai_tool

from .commands import apply_change_set, apply_lanes, apply_layout
from .imagine import generate_image
from .specs import (
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


def _to_size(size: dict[str, Any] | None) -> dict[str, int] | None:
    if not size:
        return None
    return {"w": size["span"], "h": HEIGHT_ROWS[size["height"]]}


class ChartInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: ChartSpec


class KpiInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: KpiSpec


class TableInput(ToolInput):
    title: str = TITLE_FIELD
    provenance: Provenance
    size: Size | None = SIZE_FIELD
    spec: TableSpec


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


class HeroInput(ToolInput):
    title: str
    spec: HeroSpec
    size: Size | None = SIZE_FIELD


class LabelInput(ToolInput):
    title: str
    spec: LabelSpec
    size: Size | None = SIZE_FIELD


class ImageInput(ToolInput):
    title: str
    prompt: str = Field(
        description="Detailed visual prompt. Describe style, composition, palette."
    )
    quality: bool | None = Field(default=None, description="Slower, higher fidelity.")
    size: Size | None = SIZE_FIELD


class UpdateWidgetInput(ToolInput):
    widgetId: str
    title: str | None = None
    spec: dict[str, Any] | None = Field(
        default=None, description="Full replacement spec matching the widget's kind."
    )


class RemoveWidgetInput(ToolInput):
    widgetId: str


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


class RemoveChartSeriesInput(ToolInput):
    widgetId: str
    names: list[str] = Field(
        min_length=1,
        description="Series names to drop, as they appear in the current chart.",
    )


class LayoutItem(ToolInput):
    widgetId: str
    span: int = Field(
        ge=2, le=12, description="Relative width. Spans in a row should sum to 12."
    )


class LayoutRow(ToolInput):
    height: Literal["kpi", "short", "standard", "tall"] = Field(
        description="kpi for a metric strip, standard for most chart rows, tall for dense forms."
    )
    items: list[LayoutItem] = Field(
        min_length=1, max_length=4,
        description="At most four cards per row, in left-to-right order.",
    )


class LayoutInput(ToolInput):
    rows: list[LayoutRow] = Field(min_length=1, max_length=12)


class LaneItem(ToolInput):
    widgetId: str
    height: Literal["label", "kpi", "short", "standard", "tall"]


class Lane(ToolInput):
    span: int = Field(
        ge=2, le=12, description="Relative width; spans across lanes should sum to 12."
    )
    items: list[LaneItem] = Field(
        min_length=1, max_length=8, description="Cards stacked top to bottom in this lane."
    )


class LanesInput(ToolInput):
    lanes: list[Lane] = Field(min_length=2, max_length=6)


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits $defs/$ref; the tool-schema validator wants a self-contained object."""
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = copy.deepcopy(defs.get(ref.split("/")[-1], {}))
                rest = {k: v for k, v in node.items() if k != "$ref"}
                return resolve({**target, **rest})
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return _inline_refs(model.model_json_schema(by_alias=True))


class ToolSpec:
    """A tool definition plus the handler that runs when Grok calls it."""

    def __init__(
        self,
        name: str,
        description: str,
        model: type[BaseModel],
        handler: Callable[..., Awaitable[dict[str, Any]]],
    ):
        self.name = name
        self.description = description
        self.model = model
        self.handler = handler

    def definition(self):
        return xai_tool(
            name=self.name, description=self.description, parameters=_schema(self.model)
        )


class TurnFlags:
    def __init__(self) -> None:
        self.mutated = False
        self.laid_out = False


def build_tools(
    canvas_id: str, on_change: Callable[[], None], turn: TurnFlags
) -> dict[str, ToolSpec]:
    async def place(op: dict[str, Any]) -> dict[str, Any]:
        if op["kind"] == "add_widget":
            turn.mutated = True
        result = await apply_change_set(canvas_id, [op], "agent")
        on_change()
        if result["errors"]:
            return {"ok": False, "errors": result["errors"]}
        applied = result["applied"]
        return {"ok": True, "widgetId": applied[0]["widgetId"] if applied else None}

    def adder(widget_kind: str, default_provenance: dict[str, Any] | None = None):
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            return await place(
                {
                    "kind": "add_widget",
                    "widgetKind": widget_kind,
                    "title": args["title"],
                    "spec": args["spec"],
                    "provenance": args.get("provenance") or default_provenance,
                    "size": _to_size(args.get("size")),
                }
            )

        return run

    async def run_set_style(args: dict[str, Any]) -> dict[str, Any]:
        # Style is routed through the change-set machinery so it version-bumps and undoes
        # like anything else — same invariant as every other edit.
        result = await apply_change_set(
            canvas_id, [{"kind": "set_style", "style": args}], "agent"
        )
        on_change()
        if result["errors"]:
            return {"ok": False, "errors": result["errors"]}
        return {"ok": True, "applied": args["name"]}

    async def run_generate_image(args: dict[str, Any]) -> dict[str, Any]:
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

    async def run_update_widget(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {
                "kind": "update_widget",
                "widgetId": args["widgetId"],
                "title": args.get("title"),
                "spec": args.get("spec"),
            }
        )

    async def run_remove_widget(args: dict[str, Any]) -> dict[str, Any]:
        return await place({"kind": "remove_widget", "widgetId": args["widgetId"]})

    async def run_add_chart_series(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {"kind": "add_chart_series", "widgetId": args["widgetId"], "series": args["series"]}
        )

    async def run_remove_chart_series(args: dict[str, Any]) -> dict[str, Any]:
        return await place(
            {"kind": "remove_chart_series", "widgetId": args["widgetId"], "names": args["names"]}
        )

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

    specs = [
        ToolSpec(
            "set_style",
            "Give this canvas its own visual identity before building — an accent drawn from "
            "the subject, a typographic voice, a paper tint, a card treatment. Call it once, "
            "first. The default look is the absence of a design decision.",
            StyleSpec,
            run_set_style,
        ),
        ToolSpec(
            "add_chart",
            "Place a chart on the canvas. Your primary tool — reach for it first.",
            ChartInput,
            adder("chart"),
        ),
        ToolSpec(
            "add_kpi",
            "Place a single decisive number with an optional comparison and sparkline. Use for "
            "headline metrics instead of stating them in text.",
            KpiInput,
            adder("kpi"),
        ),
        ToolSpec(
            "add_table",
            "Place an exact-values table. Use when precision matters more than shape.",
            TableInput,
            adder("table"),
        ),
        ToolSpec(
            "add_narrative",
            "Place a short annotation card — the 'so what', a caveat, or a recommendation. "
            "This is where prose belongs.",
            NarrativeInput,
            adder("narrative"),
        ),
        ToolSpec(
            "add_hero",
            "Open the canvas with its thesis in display type — one huge number or claim with a "
            "supporting line. A story leads with its hero; place it first, full width, height "
            "'kpi'. One per canvas.",
            HeroInput,
            adder("hero"),
        ),
        ToolSpec(
            "add_label",
            "Place a section heading — a bare title band with no card chrome. Use these to name "
            "the stages of a flow, or to group a canvas into labelled bands. Give it height 'label'.",
            LabelInput,
            adder("label"),
        ),
        ToolSpec(
            "add_statement",
            "Place a financial statement ledger — ordered lines with +, - and = markers, "
            "subtotals and a highlighted total. Use for a P&L build, a cash bridge, or any figure "
            "built up from components. This is not a table; it is a calculation.",
            StatementInput,
            adder("statement"),
        ),
        ToolSpec(
            "generate_image",
            "Generate an image with Grok Imagine and place it on the canvas. Use for concepts, "
            "moods, diagrams-as-art, or anything better shown than plotted.",
            ImageInput,
            run_generate_image,
        ),
        ToolSpec(
            "update_widget",
            "Revise a widget already on the canvas by id. Takes a FULL replacement spec — you "
            "must include every field you want to keep. Use for title changes, KPI value tweaks, "
            "narrative rewrites, table row edits. Do NOT use to add or remove chart series — "
            "use add_chart_series / remove_chart_series instead, which preserve the rest of the "
            "chart without you needing to know the existing data.",
            UpdateWidgetInput,
            run_update_widget,
        ),
        ToolSpec(
            "add_chart_series",
            "Append one or more series to an existing chart, preserving all the other series and "
            "axes. Use this when the user says 'add X to the chart', 'compare with Y', 'overlay Z'. "
            "Each series' data array must be aligned to the chart's existing xAxis categories "
            "(same length, same order); look up the current chart in the canvas summary before "
            "you fetch or fabricate the new data. Duplicate names are ignored.",
            AddChartSeriesInput,
            run_add_chart_series,
        ),
        ToolSpec(
            "remove_chart_series",
            "Drop one or more series from an existing chart by name, keeping everything else "
            "intact.",
            RemoveChartSeriesInput,
            run_remove_chart_series,
        ),
        ToolSpec(
            "set_layout",
            "Arrange the whole canvas as a dashboard. Pass an ordered list of rows; every card in "
            "a row shares one height and the spans are fitted to exactly 12 columns. Call this "
            "once, last, after all widgets exist. This is how a canvas stops looking like a pile "
            "of boxes.",
            LayoutInput,
            run_set_layout,
        ),
        ToolSpec(
            "set_lanes",
            "Arrange the canvas as side-by-side vertical columns instead of rows. This is the "
            "shape a flow, pipeline or stage diagram needs: one lane per stage, each holding a "
            "labelled heading and the cards for that stage. Use instead of set_layout, not as "
            "well as it.",
            LanesInput,
            run_set_lanes,
        ),
        ToolSpec(
            "remove_widget",
            "Take a widget off the canvas. Reversible via undo.",
            RemoveWidgetInput,
            run_remove_widget,
        ),
    ]
    return {spec.name: spec for spec in specs}


async def execute(spec: ToolSpec, raw_args: dict[str, Any]) -> dict[str, Any]:
    """Validate the model's arguments before anything touches the command layer."""
    try:
        parsed = spec.model.model_validate(raw_args)
    except ValidationError as e:
        issues = "; ".join(
            f'{".".join(str(p) for p in i["loc"])} {i["msg"]}' for i in e.errors()
        )
        return {"ok": False, "errors": [f"{spec.name}: {issues}"]}
    args = parsed.model_dump(mode="json", by_alias=True, exclude_none=True)
    return await spec.handler(args)

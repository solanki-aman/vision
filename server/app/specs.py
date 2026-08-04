"""Widget specs. Pydantic is the single definition: tool schemas and command
validation both come from these models, so the model cannot emit renderer code —
only data the adapter knows how to draw."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CHART_TYPES = Literal[
    # over time / across categories — needs xAxis.categories + series
    "line", "area", "stacked_area", "step_line", "bar", "horizontal_bar",
    "stacked_bar", "stacked_horizontal_bar", "pictorial_bar", "scatter",
    "diverging_bar", "bullet", "slope",
    "effect_scatter", "bubble", "waterfall", "theme_river",
    # part of a whole — needs xAxis.categories + one series
    "pie", "donut", "rose", "funnel", "gauge",
    # hierarchy — needs `hierarchy`
    "treemap", "sunburst", "tree",
    # relationships — needs `links`
    "sankey", "graph", "chord",
    # distributions and matrices
    "boxplot", "candlestick", "heatmap", "calendar", "radar", "parallel",
]

WIDGET_KINDS = (
    "chart", "kpi", "table", "narrative", "image",
    "label", "statement", "hero",
)


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class XAxis(Spec):
    label: str | None = None
    categories: list[str]


class YAxis(Spec):
    label: str | None = None
    unit: str | None = None


class Series(Spec):
    name: str
    data: list[float | None]


class Link(Spec):
    from_: str = Field(alias="from")
    to: str
    value: float

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class HierarchyNode(Spec):
    name: str
    parent: str | None = Field(default=None, description="Omit for root nodes.")
    value: float | None = Field(
        default=None, description="Leaf size. Parents are summed when omitted."
    )


class Ohlc(Spec):
    date: str
    open: float
    high: float
    low: float
    close: float


class Box(Spec):
    name: str
    min: float
    q1: float
    median: float
    q3: float
    max: float


class CalendarDay(Spec):
    date: str = Field(description="YYYY-MM-DD")
    value: float


class Annotation(Spec):
    kind: Literal["reference_line", "moment", "era", "callout"] = Field(
        description=(
            "reference_line: horizontal line at `value` (target, floor, average). "
            "moment: vertical line at category `at` (an event). "
            "era: shaded band from `from` to `to` (a period). "
            "callout: label pinned at category `at` and y `value`."
        )
    )
    label: str = Field(description="Short annotation text, a few words.")
    value: float | None = None
    at: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ChartSpec(Spec):
    chartType: CHART_TYPES
    xAxis: XAxis | None = Field(
        default=None,
        description="Category or time axis. Required for cartesian, part-of-whole, radar and heatmap types.",
    )
    yAxis: YAxis | None = None
    series: list[Series] = Field(
        default_factory=list,
        description=(
            "One entry per line/bar/slice group, aligned to xAxis.categories. "
            "For heatmap, one entry per row. For bubble, the second series carries point sizes."
        ),
    )
    links: list[Link] | None = Field(
        default=None,
        description="sankey, graph and chord: weighted connections between node names.",
    )
    hierarchy: list[HierarchyNode] | None = Field(
        default=None,
        description="treemap, sunburst and tree: a flat parent-child list, not a nested object.",
    )
    ohlc: list[Ohlc] | None = Field(default=None, description="candlestick only.")
    boxes: list[Box] | None = Field(
        default=None, description="boxplot only: five-number summary per group."
    )
    calendar: list[CalendarDay] | None = Field(
        default=None, description="calendar only: one entry per day."
    )
    target: list[float] | None = Field(
        default=None,
        description="bullet only: the target for each category, aligned to xAxis.categories.",
    )
    zoom: bool | None = Field(
        default=None,
        description="Adds a draggable range slider under the chart. Use for long time series (20+ points).",
    )
    annotations: list[Annotation] | None = Field(
        default=None,
        max_length=5,
        description="Marks drawn ON the chart. Cartesian types only. This is how you point at what matters.",
    )


class HeroSpec(Spec):
    display: str = Field(
        description="The one big thing, set in display type: a number ('$823K') or a short claim, eight words at most."
    )
    dek: str | None = Field(default=None, description="One supporting sentence under the display line.")
    kicker: str | None = Field(
        default=None, description="Tiny overline above it, e.g. 'FY26 BUDGET REVIEW'."
    )


class KpiComparison(Spec):
    baseline: float = Field(
        description=(
            "The reference value to compare against, in the SAME unit as `value`. "
            "The UI computes the percent change itself."
        )
    )
    label: str = Field(
        description="Short name of the baseline only, e.g. 'China' or 'last quarter'. Not a sentence."
    )
    favorableDirection: Literal["up", "down", "neutral"] = Field(
        description="Which direction is good for this metric. Use 'neutral' when neither is."
    )


class KpiSpec(Spec):
    value: float
    unit: str | None = Field(default=None, description="Unit of `value`, e.g. '%', 'M', 'USD'.")
    label: str = Field(description="What the number measures, e.g. 'million people'.")
    comparison: KpiComparison | None = Field(
        default=None,
        description="Omit entirely unless there is a genuine like-for-like baseline.",
    )
    sparkline: list[float] | None = Field(
        default=None, description="Recent values of this same metric, oldest first."
    )


class Column(Spec):
    key: str
    label: str
    align: Literal["left", "right"] | None = None


class TableSpec(Spec):
    columns: list[Column]
    rows: list[dict[str, str | float | None]]


class NarrativeSpec(Spec):
    body: str = Field(description="2-5 short sentences. Markdown-free plain text.")
    bullets: list[str] | None = Field(default=None, max_length=5)
    tone: Literal["neutral", "positive", "caution", "critical"] = "neutral"


class LabelSpec(Spec):
    text: str = Field(description="Short section heading, e.g. 'LAYER 1 — SOURCE ACCOUNTS'.")
    note: str | None = Field(default=None, description="One short line under the heading.")


class StatementLine(Spec):
    label: str
    value: float
    role: Literal["add", "subtract", "subtotal", "total"] = Field(
        description="add renders +, subtract renders -, subtotal and total render = with a rule above."
    )
    percent: float | None = Field(default=None, description="Share of the reference line, 0-100.")
    indent: bool | None = None


class StatementSpec(Spec):
    unit: str | None = Field(default=None, description="e.g. 'USD M'.")
    lines: list[StatementLine] = Field(min_length=2, max_length=24)


class ImageSpec(Spec):
    url: str
    prompt: str


class StyleSpec(Spec):
    name: str = Field(
        description="Name this canvas's visual identity like an art movement, 1-3 words: 'Signal Red', 'Ledger Quiet', 'Midnight Cadence'."
    )
    accent: str = Field(
        pattern=r"^#[0-9a-fA-F]{6}$",
        description=(
            "One accent hex drawn from the subject itself — the brand, the material, the mood. "
            "Colours chrome, labels and highlights; never chart series."
        ),
    )
    type: Literal["sans", "serif", "mono"] = Field(
        description="Display voice for the hero and titles. serif reads editorial essay; mono reads technical dossier; sans reads product."
    )
    paper: Literal["default", "cream", "cool", "sage", "blush"] = Field(
        description="Background tint of the whole canvas."
    )
    cards: Literal["bordered", "flat", "ghost"] = Field(
        description="bordered = product cards; flat = soft panels without borders; ghost = widgets sit directly on the paper, gallery-style."
    )
    reason: str = Field(description="One sentence on why this identity fits the subject.")


class Provenance(Spec):
    source: str = Field(
        description="Where the numbers came from, e.g. 'Live web search' or 'Illustrative'"
    )
    asOf: str | None = None
    confidence: Literal["measured", "estimated", "illustrative"]
    note: str | None = None


SPEC_BY_KIND: dict[str, type[Spec]] = {
    "chart": ChartSpec,
    "kpi": KpiSpec,
    "table": TableSpec,
    "narrative": NarrativeSpec,
    "image": ImageSpec,
    "label": LabelSpec,
    "statement": StatementSpec,
    "hero": HeroSpec,
}


def validate_spec(kind: str, spec: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Returns (validated spec, error message). One of the two is always None."""
    model = SPEC_BY_KIND.get(kind)
    if model is None:
        return None, f"unknown widget kind {kind}"
    try:
        parsed = model.model_validate(spec)
    except ValidationError as e:
        issues = "; ".join(f'{".".join(str(p) for p in i["loc"])} {i["msg"]}' for i in e.errors())
        return None, issues
    return parsed.model_dump(mode="json", by_alias=True, exclude_none=True), None

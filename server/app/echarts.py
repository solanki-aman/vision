"""The ECharts compiler, ported from ``web/src/chartAdapter.ts`` and exposed as a
LangChain tool.

A typed ``ChartSpec`` goes in; a JSON-serialisable ``EChartsOption`` comes out —
the same structure the browser builds, minus the live JS formatter closures the
TS adapter injects (those cannot cross a JSON boundary, so the interactive client
keeps its own themed render). Two jobs:

1. ``build_option`` is the canonical, catalogue-complete spec -> option transform.
2. ``build_echarts_option`` wraps it as a ``StructuredTool`` so the chart
   create/update handlers can compile a spec at write time — proving it renders
   before it reaches the canvas, and returning the option as a first-class,
   agent-produced artifact.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from .specs import ChartSpec

FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"
LABEL = 11
MICRO = 10

# A self-contained default theme (VISION palette, light ink) so the compiler runs
# standalone. The browser passes its own live theme; this mirrors theme.ts.
DEFAULT_THEME: dict[str, Any] = {
    "mode": "light",
    "gradient": False,
    "series": [
        "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
        "#e87ba4", "#008300", "#4a3aa7", "#e34948",
    ],
    "sequential": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
    "ink": {
        "surface": "#ffffff",
        "primary": "#16150f",
        "secondary": "#55534b",
        "muted": "#84827a",
        "grid": "#ebe9e3",
        "axis": "#d5d2ca",
        "border": "rgba(20,19,15,0.10)",
    },
}

ITEM_TRIGGER = {
    "pie", "donut", "rose", "funnel", "gauge", "scatter", "effect_scatter", "bubble",
    "heatmap", "calendar", "sankey", "graph", "chord", "treemap", "sunburst", "tree",
    "parallel", "theme_river",
}

NO_LEGEND = {
    "waterfall", "diverging_bar", "bullet", "heatmap", "calendar", "gauge", "treemap",
    "sunburst", "tree", "sankey", "graph", "chord", "funnel", "boxplot", "candlestick",
}


def clip(n: int, s: str) -> str:
    return f"{s[: n - 1]}…" if len(s) > n else s


def abbrev(n: float) -> str:
    a = abs(n)
    if a >= 1e12:
        return f"{round(n / 1e12, 1):g}T"
    if a >= 1e9:
        return f"{round(n / 1e9, 1):g}B"
    if a >= 1e6:
        return f"{round(n / 1e6, 1):g}M"
    if a >= 1e3:
        return f"{round(n / 1e3, 1):g}K"
    return f"{round(n, 2):g}"


def _to_tree(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat parent-child list -> nested tree (same shape ECharts wants)."""
    nodes: dict[str, dict[str, Any]] = {
        i["name"]: {"name": i["name"], "value": i.get("value"), "children": []} for i in items
    }
    roots: list[dict[str, Any]] = []
    for i in items:
        node = nodes[i["name"]]
        parent = nodes.get(i.get("parent")) if i.get("parent") else None
        if parent is not None and parent is not node:
            parent["children"].append(node)
        else:
            roots.append(node)

    def prune(n: dict[str, Any]) -> dict[str, Any]:
        if n["children"]:
            return {"name": n["name"], "children": [prune(c) for c in n["children"]]}
        return {"name": n["name"], "value": n["value"] if n["value"] is not None else 0}

    return [prune(r) for r in roots]


def _wash(hex_color: str, mode: str) -> dict[str, Any]:
    tail = "3d" if mode == "dark" else "33"
    return {
        "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
        "colorStops": [
            {"offset": 0, "color": hex_color},
            {"offset": 1, "color": f"{hex_color}{tail}"},
        ],
    }


def build_option(
    spec: dict[str, Any],
    theme: dict[str, Any] | None = None,
    animate: bool = True,
    entities: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Typed spec in, renderer option out — the model never supplies ECharts config."""
    theme = theme or DEFAULT_THEME
    entities = entities or {}
    t = spec["chartType"]
    SERIES = theme["series"]
    SEQUENTIAL = theme["sequential"]
    INK = theme["ink"]
    mode = theme["mode"]

    def fill(i: int) -> Any:
        return _wash(SERIES[i % len(SERIES)], mode) if theme.get("gradient") else SERIES[i % len(SERIES)]

    cat_axis = {
        "type": "category",
        "axisTick": {"show": False},
        "axisLine": {"lineStyle": {"color": INK["axis"]}},
        "axisLabel": {"color": INK["muted"], "fontSize": LABEL, "fontFamily": FONT, "hideOverlap": True},
        "splitLine": {"show": False},
        "nameTextStyle": {"color": INK["muted"], "fontSize": MICRO},
    }
    val_axis = {
        "type": "value",
        "axisTick": {"show": False},
        "axisLine": {"show": False},
        "axisLabel": {"color": INK["muted"], "fontSize": LABEL, "fontFamily": FONT},
        "splitLine": {"lineStyle": {"color": INK["grid"], "width": 1}},
        "nameTextStyle": {"color": INK["muted"], "fontSize": MICRO},
    }
    legend_base = {
        "type": "scroll", "bottom": 0, "icon": "roundRect",
        "itemWidth": 8, "itemHeight": 8, "itemGap": 14,
        "textStyle": {"color": INK["secondary"], "fontSize": LABEL, "fontFamily": FONT},
        "pageTextStyle": {"color": INK["muted"], "fontSize": MICRO},
        "pageIconColor": INK["secondary"], "pageIconInactiveColor": INK["axis"], "pageIconSize": 9,
    }

    cats = (spec.get("xAxis") or {}).get("categories") or []
    series = spec.get("series") or []
    unit = f" {spec['yAxis']['unit']}" if (spec.get("yAxis") or {}).get("unit") else ""
    show_legend = len(series) > 1 and t not in NO_LEGEND
    bottom_gap = 34 if show_legend else 6

    base: dict[str, Any] = {
        "color": SERIES,
        "backgroundColor": "transparent",
        "animation": animate,
        "animationDuration": 700,
        "animationEasing": "cubicOut",
        "textStyle": {"color": INK["secondary"], "fontFamily": FONT, "fontSize": LABEL},
        "tooltip": {
            "trigger": "item" if t in ITEM_TRIGGER else "axis",
            "backgroundColor": "rgba(18,18,17,0.96)" if mode == "dark" else "rgba(255,255,255,0.97)",
            "borderColor": INK["border"], "borderWidth": 1, "padding": [9, 12],
            "textStyle": {"color": INK["primary"], "fontSize": 12, "fontFamily": FONT},
            "axisPointer": {"type": "line", "lineStyle": {"color": INK["axis"], "width": 1}},
        },
        "legend": legend_base if show_legend else None,
        "grid": {
            "left": 6, "right": 14, "top": 10,
            "bottom": bottom_gap + (30 if spec.get("zoom") else 0), "containLabel": True,
        },
    }
    if spec.get("zoom"):
        filler = "rgba(57,135,229,0.22)" if mode == "dark" else "rgba(42,120,214,0.16)"
        base["dataZoom"] = [
            {"type": "inside"},
            {
                "type": "slider", "height": 18, "bottom": 28 if show_legend else 4,
                "borderColor": "transparent", "backgroundColor": INK["grid"], "fillerColor": filler,
                "handleStyle": {"color": SERIES[0], "borderColor": SERIES[0]},
                "moveHandleStyle": {"color": SERIES[0]},
                "dataBackground": {"lineStyle": {"color": INK["axis"]}, "areaStyle": {"color": "transparent"}},
                "selectedDataBackground": {"lineStyle": {"color": SERIES[0]}, "areaStyle": {"color": "transparent"}},
                "textStyle": {"color": INK["muted"], "fontSize": MICRO},
            },
        ]

    # Marks drawn on the plot.
    anno: dict[str, Any] = {}
    line_data, area_data, point_data = [], [], []
    for n in spec.get("annotations") or []:
        kind = n.get("kind")
        if kind == "reference_line" and n.get("value") is not None:
            line_data.append({"yAxis": n["value"], "name": n["label"]})
        elif kind == "moment" and n.get("at"):
            line_data.append({"xAxis": n["at"], "name": n["label"]})
        elif kind == "era" and n.get("from") and n.get("to"):
            area_data.append([{"xAxis": n["from"], "name": n["label"]}, {"xAxis": n["to"]}])
        elif kind == "callout" and n.get("at") and n.get("value") is not None:
            point_data.append({"coord": [n["at"], n["value"]], "name": n["label"]})
    if line_data:
        anno["markLine"] = {
            "silent": True, "symbol": "none", "animation": False,
            "lineStyle": {"color": INK["muted"], "type": "dashed", "width": 1},
            "label": {"color": INK["muted"], "fontSize": MICRO, "fontFamily": FONT, "position": "insideEndTop"},
            "data": line_data,
        }
    if area_data:
        area_col = "rgba(255,255,255,0.05)" if mode == "dark" else "rgba(20,19,15,0.045)"
        anno["markArea"] = {
            "silent": True, "animation": False, "itemStyle": {"color": area_col},
            "label": {"color": INK["muted"], "fontSize": MICRO, "fontFamily": FONT, "position": "insideTop"},
            "data": area_data,
        }
    if point_data:
        anno["markPoint"] = {
            "silent": True, "animation": False, "symbol": "circle", "symbolSize": 7,
            "itemStyle": {"color": INK["primary"], "borderColor": INK["surface"], "borderWidth": 2},
            "label": {"color": INK["primary"], "fontSize": MICRO, "fontFamily": FONT, "fontWeight": 600, "position": "top", "distance": 8},
            "data": point_data,
        }

    def cartesian(x: dict[str, Any], y: dict[str, Any]) -> dict[str, Any]:
        return {**base, "xAxis": {**x, "name": None}, "yAxis": y}

    def rev(seq: list[Any]) -> list[Any]:
        return list(reversed(seq))

    # ---- cartesian line family ------------------------------------------------
    if t in ("line", "area", "stacked_area", "step_line"):
        out = cartesian({**cat_axis, "data": cats, "boundaryGap": False}, {**val_axis, "name": (spec.get("yAxis") or {}).get("label")})
        built = []
        for i, s in enumerate(series):
            ec = entities.get(s["name"])
            item: dict[str, Any] = {
                "type": "line", "name": s["name"], "data": s["data"],
                "symbolSize": 6, "showSymbol": len(s["data"]) <= 20,
                "lineStyle": {"width": 2, **({"color": ec} if ec else {})},
                "emphasis": {"focus": "series"},
            }
            if t == "step_line":
                item["step"] = "middle"
            if t == "stacked_area":
                item["stack"] = "total"
            if ec:
                item["itemStyle"] = {"color": ec}
            if t in ("area", "stacked_area"):
                item["areaStyle"] = {"opacity": 0.75 if t == "stacked_area" else 0.22, "color": ec or fill(i)}
            if i == 0:
                item.update(anno)
            built.append(item)
        out["series"] = built
        return out

    # ---- bar / stacked_bar ----------------------------------------------------
    if t in ("bar", "stacked_bar"):
        out = cartesian({**cat_axis, "data": cats}, {**val_axis, "name": (spec.get("yAxis") or {}).get("label")})
        built = []
        for i, s in enumerate(series):
            if len(series) == 1 and entities:
                data = [
                    {"value": v, **({"itemStyle": {"color": entities[cats[j]]}} if j < len(cats) and entities.get(cats[j]) else {})}
                    for j, v in enumerate(s["data"])
                ]
            else:
                data = s["data"]
            item = {
                "type": "bar", "name": s["name"], "data": data,
                "itemStyle": {
                    "color": entities.get(s["name"]) or fill(i),
                    "borderRadius": 2 if t == "stacked_bar" else [4, 4, 0, 0],
                },
                "barMaxWidth": 44, "emphasis": {"focus": "series"},
            }
            if t == "stacked_bar":
                item["stack"] = "total"
            if i == 0:
                item.update(anno)
            built.append(item)
        out["series"] = built
        return out

    # ---- horizontal_bar -------------------------------------------------------
    if t in ("horizontal_bar", "stacked_horizontal_bar"):
        built = []
        for i, s in enumerate(series):
            if len(series) == 1 and entities:
                rcats = rev(cats)
                data = [
                    {"value": v, **({"itemStyle": {"color": entities[rcats[j]]}} if j < len(rcats) and entities.get(rcats[j]) else {})}
                    for j, v in enumerate(rev(s["data"]))
                ]
            else:
                data = rev(s["data"])
            item = {
                "type": "bar", "name": s["name"], "data": data,
                "itemStyle": {"color": entities.get(s["name"]) or fill(i), "borderRadius": 2 if t == "stacked_horizontal_bar" else [0, 4, 4, 0]},
                "barMaxWidth": 26, "emphasis": {"focus": "series"},
            }
            if t == "stacked_horizontal_bar":
                item["stack"] = "total"
            built.append(item)
        return {
            **base,
            "grid": {"left": 6, "right": 18, "top": 10, "bottom": bottom_gap + 12, "containLabel": True},
            "xAxis": {**val_axis, "name": (spec.get("yAxis") or {}).get("label"), "nameLocation": "middle", "nameGap": 28},
            "yAxis": {**cat_axis, "data": rev(cats)},
            "series": built,
        }

    # ---- diverging_bar --------------------------------------------------------
    if t == "diverging_bar":
        values = [v or 0 for v in (series[0]["data"] if series else [])]
        good, bad = SERIES[2], SERIES[7]
        return {
            **base,
            "grid": {"left": 6, "right": 24, "top": 10, "bottom": bottom_gap + 4, "containLabel": True},
            "xAxis": {**val_axis, "name": (spec.get("yAxis") or {}).get("label"), "nameLocation": "middle", "nameGap": 26, "axisLine": {"show": True, "lineStyle": {"color": INK["axis"]}}},
            "yAxis": {**cat_axis, "data": rev(cats), "axisLine": {"show": False}},
            "series": [{
                "type": "bar",
                "data": [{"value": v, "itemStyle": {"color": good if v >= 0 else bad, "borderRadius": [0, 4, 4, 0] if v >= 0 else [4, 0, 0, 4]}} for v in rev(values)],
                "barMaxWidth": 24,
                "label": {"show": len(values) <= 14, "position": "right", "color": INK["muted"], "fontSize": MICRO},
            }],
        }

    # ---- bullet ---------------------------------------------------------------
    if t == "bullet":
        actual = [v or 0 for v in (series[0]["data"] if series else [])]
        target = spec.get("target") or [v or 0 for v in (series[1]["data"] if len(series) > 1 else [])]
        rtarget = rev(target)
        return {
            **base,
            "grid": {"left": 6, "right": 24, "top": 10, "bottom": bottom_gap + 4, "containLabel": True},
            "xAxis": {**val_axis, "name": (spec.get("yAxis") or {}).get("label")},
            "yAxis": {**cat_axis, "data": rev(cats)},
            "series": [
                {
                    "type": "bar", "name": series[0]["name"] if series else "Actual",
                    "data": [{"value": v, "itemStyle": {"color": (SERIES[2] if v >= (rtarget[i] if i < len(rtarget) else 0) else SERIES[3]) if target else SERIES[0], "borderRadius": [0, 3, 3, 0]}} for i, v in enumerate(rev(actual))],
                    "barMaxWidth": 16, "z": 2,
                },
                {
                    "type": "scatter", "name": "Target",
                    "data": [[v, i] for i, v in enumerate(rtarget)],
                    "symbol": "rect", "symbolSize": [3, 22], "itemStyle": {"color": INK["primary"]}, "z": 3,
                },
            ],
        }

    # ---- slope ----------------------------------------------------------------
    if t == "slope":
        return {
            **base,
            "grid": {"left": 8, "right": 74, "top": 14, "bottom": bottom_gap + 4, "containLabel": True},
            "xAxis": {**cat_axis, "data": cats[:2], "boundaryGap": False},
            "yAxis": {**val_axis, "name": (spec.get("yAxis") or {}).get("label"), "splitLine": {"show": False}},
            "legend": None,
            "series": [{
                "type": "line", "name": s["name"], "data": s["data"][:2], "symbolSize": 8,
                "lineStyle": {"width": 2}, "itemStyle": {"color": entities.get(s["name"]) or SERIES[i % len(SERIES)]},
                "endLabel": {"show": True, "color": INK["secondary"], "fontSize": MICRO, "fontFamily": FONT},
                "emphasis": {"focus": "series"},
            } for i, s in enumerate(series)],
        }

    # ---- pictorial_bar --------------------------------------------------------
    if t == "pictorial_bar":
        out = cartesian({**cat_axis, "data": cats}, {**val_axis, "name": (spec.get("yAxis") or {}).get("label")})
        out["series"] = [{
            "type": "pictorialBar", "name": s["name"], "data": s["data"],
            "symbol": "roundRect", "symbolRepeat": True, "symbolSize": ["70%", 5], "symbolMargin": 3, "symbolClip": False,
        } for s in series]
        return out

    # ---- waterfall ------------------------------------------------------------
    if t == "waterfall":
        values = [v or 0 for v in (series[0]["data"] if series else [])]
        support, run = [], 0.0
        for v in values:
            support.append(run if v >= 0 else run + v)
            run += v
        out = cartesian({**cat_axis, "data": cats}, {**val_axis, "name": (spec.get("yAxis") or {}).get("label")})
        out["series"] = [
            {"type": "bar", "stack": "wf", "itemStyle": {"color": "transparent"}, "data": support, "silent": True},
            {
                "type": "bar", "stack": "wf", "barMaxWidth": 44,
                "data": [{"value": abs(v), "itemStyle": {"color": SERIES[2] if v >= 0 else SERIES[7], "borderRadius": 3}} for v in values],
                "label": {"show": len(values) <= 10, "position": "top", "color": INK["secondary"], "fontSize": MICRO},
            },
        ]
        return out

    # ---- scatter family -------------------------------------------------------
    if t in ("scatter", "effect_scatter", "bubble"):
        sizes = series[1]["data"] if (t == "bubble" and len(series) > 1) else []
        max_size = max([1.0] + [abs(v or 0) for v in sizes])
        picked = series[:1] if t == "bubble" else series[:4]
        out = cartesian({**cat_axis, "data": cats, "name": (spec.get("xAxis") or {}).get("label")}, {**val_axis, "name": (spec.get("yAxis") or {}).get("label")})
        built = []
        for s in picked:
            item = {
                "type": "effectScatter" if t == "effect_scatter" else "scatter",
                "name": s["name"], "data": [[i, v] for i, v in enumerate(s["data"])],
                "itemStyle": {"opacity": 0.85},
            }
            if t == "effect_scatter":
                item["rippleEffect"] = {"scale": 2.4}
            item["symbolSize"] = 10  # bubble sizing uses a JS callback in the client; static here
            built.append(item)
        out["series"] = built
        return out

    # ---- theme_river ----------------------------------------------------------
    if t == "theme_river":
        data = []
        for s in series:
            for i, v in enumerate(s["data"]):
                data.append([i, v or 0, s["name"]])
        return {
            **base,
            "singleAxis": {
                "type": "value", "min": 0, "max": max(1, len(cats) - 1), "interval": 1,
                "left": 10, "right": 16, "top": 12, "bottom": bottom_gap + 18,
                "axisTick": {"show": False}, "axisLine": {"lineStyle": {"color": INK["axis"]}},
                "splitLine": {"show": False},
                "axisLabel": {"color": INK["muted"], "fontSize": LABEL, "fontFamily": FONT},
            },
            "series": [{"type": "themeRiver", "data": data, "label": {"show": False}, "emphasis": {"focus": "series"}, "itemStyle": {"shadowBlur": 0}}],
        }

    # ---- pie / donut / rose ---------------------------------------------------
    if t in ("pie", "donut", "rose"):
        s = series[0] if series else {"name": "", "data": []}
        radius = ["46%", "72%"] if t == "donut" else (["18%", "74%"] if t == "rose" else "70%")
        return {
            **base,
            "legend": {**legend_base, "show": len(cats) <= 8},
            "series": [{
                "type": "pie", "name": s["name"],
                **({"roseType": "area"} if t == "rose" else {}),
                "radius": radius, "center": ["50%", "44%"],
                "itemStyle": {"borderColor": INK["surface"], "borderWidth": 2, "borderRadius": 3},
                "label": {"color": INK["secondary"], "fontSize": MICRO},
                "labelLine": {"lineStyle": {"color": INK["axis"]}, "length": 6, "length2": 8},
                "labelLayout": {"hideOverlap": True}, "emphasis": {"scaleSize": 6},
                "data": [
                    {"name": c, "value": (s["data"][i] if i < len(s["data"]) else 0),
                     **({"itemStyle": {"color": entities[c], "borderColor": INK["surface"], "borderWidth": 2, "borderRadius": 3}} if entities.get(c) else {})}
                    for i, c in enumerate(cats)
                ],
            }],
        }

    # ---- funnel ---------------------------------------------------------------
    if t == "funnel":
        s = series[0] if series else {"name": "", "data": []}
        return {
            **base,
            "series": [{
                "type": "funnel", "name": s["name"], "left": "8%", "right": "8%", "top": 8, "bottom": 8,
                "gap": 2, "minSize": "18%", "label": {"color": INK["primary"], "fontSize": LABEL},
                "itemStyle": {"borderColor": INK["surface"], "borderWidth": 2},
                "data": [{"name": c, "value": (s["data"][i] if i < len(s["data"]) else 0)} for i, c in enumerate(cats)],
            }],
        }

    # ---- gauge ----------------------------------------------------------------
    if t == "gauge":
        v = series[0]["data"][0] if (series and series[0]["data"]) else 0
        return {
            **base,
            "series": [{
                "type": "gauge", "startAngle": 205, "endAngle": -25, "min": 0, "max": 100,
                "radius": "76%", "center": ["50%", "60%"],
                "progress": {"show": True, "width": 11, "roundCap": True, "itemStyle": {"color": SERIES[0]}},
                "axisLine": {"roundCap": True, "lineStyle": {"width": 11, "color": [[1, INK["grid"]]]}},
                "axisTick": {"show": False}, "splitLine": {"show": False}, "axisLabel": {"show": False},
                "pointer": {"show": False}, "title": {"show": False},
                "detail": {"valueAnimation": True, "color": INK["primary"], "fontSize": 26, "fontWeight": 600, "fontFamily": FONT, "offsetCenter": [0, "26%"], "formatter": f"{{value}}{(spec.get('yAxis') or {}).get('unit') or ''}"},
                "data": [{"value": round(v or 0, 1)}],
            }],
        }

    # ---- treemap / sunburst / tree -------------------------------------------
    if t == "treemap":
        return {
            **base,
            "series": [{
                "type": "treemap", "data": _to_tree(spec.get("hierarchy") or []),
                "roam": False, "nodeClick": False, "breadcrumb": {"show": False},
                "label": {"color": "#fff", "fontSize": LABEL, "fontFamily": FONT},
                "upperLabel": {"show": True, "height": 20, "color": INK["secondary"], "fontSize": MICRO},
                "itemStyle": {"borderColor": INK["surface"], "borderWidth": 2, "gapWidth": 2, "borderRadius": 3},
                "levels": [{}, {"itemStyle": {"borderWidth": 2, "gapWidth": 2}}],
            }],
        }
    if t == "sunburst":
        return {
            **base,
            "series": [{
                "type": "sunburst", "data": _to_tree(spec.get("hierarchy") or []),
                "radius": ["16%", "92%"], "center": ["50%", "50%"], "nodeClick": False,
                "itemStyle": {"borderColor": INK["surface"], "borderWidth": 2},
                "label": {"color": "#fff", "fontSize": MICRO, "minAngle": 14},
            }],
        }
    if t == "tree":
        return {
            **base,
            "series": [{
                "type": "tree", "data": _to_tree(spec.get("hierarchy") or []),
                "left": 62, "right": 78, "top": 14, "bottom": 14, "symbolSize": 8, "orient": "LR",
                "expandAndCollapse": False, "itemStyle": {"color": SERIES[0], "borderWidth": 0},
                "lineStyle": {"color": INK["axis"], "width": 1, "curveness": 0.4},
                "label": {"color": INK["secondary"], "fontSize": MICRO, "position": "left", "align": "right", "distance": 6},
                "leaves": {"label": {"position": "right", "align": "left"}},
            }],
        }

    # ---- sankey / graph / chord ----------------------------------------------
    if t in ("sankey", "chord", "graph"):
        links = spec.get("links") or []
        names = list(dict.fromkeys([n for l in links for n in (l["from"], l["to"])]))
        if t == "sankey":
            return {
                **base,
                "series": [{
                    "type": "sankey", "data": [{"name": n} for n in names],
                    "links": [{"source": l["from"], "target": l["to"], "value": l["value"]} for l in links],
                    "left": 6, "right": 78, "top": 10, "bottom": 10, "emphasis": {"focus": "adjacency"},
                    "lineStyle": {"color": "gradient", "opacity": 0.32},
                    "label": {"color": INK["secondary"], "fontSize": MICRO}, "itemStyle": {"borderWidth": 0},
                }],
            }
        weight: dict[str, float] = {}
        for l in links:
            weight[l["from"]] = weight.get(l["from"], 0) + l["value"]
            weight[l["to"]] = weight.get(l["to"], 0) + l["value"]
        max_w = max([1.0] + list(weight.values()))
        return {
            **base,
            "series": [{
                "type": "graph", "layout": "circular", "circular": {"rotateLabel": False},
                "left": 54, "right": 54, "top": 20, "bottom": 20,
                "data": [{"name": n, "value": weight.get(n, 0), "symbolSize": 9 + (weight.get(n, 0) / max_w) * 22, "itemStyle": {"color": SERIES[i % len(SERIES)]}} for i, n in enumerate(names)],
                "links": [{"source": l["from"], "target": l["to"], "value": l["value"]} for l in links],
                "roam": False,
                "label": {"show": True, "position": "right", "color": INK["secondary"], "fontSize": MICRO, "fontFamily": FONT},
                "lineStyle": {"color": "source", "opacity": 0.28, "curveness": 0.3},
                "emphasis": {"focus": "adjacency", "lineStyle": {"opacity": 0.7, "width": 2}},
            }],
        }

    # ---- boxplot --------------------------------------------------------------
    if t == "boxplot":
        boxes = spec.get("boxes") or []
        out = cartesian({**cat_axis, "data": [b["name"] for b in boxes]}, {**val_axis, "name": (spec.get("yAxis") or {}).get("label")})
        out["series"] = [{
            "type": "boxplot",
            "data": [[b["min"], b["q1"], b["median"], b["q3"], b["max"]] for b in boxes],
            "itemStyle": {"color": "rgba(57,135,229,0.22)", "borderColor": SERIES[0], "borderWidth": 1.5},
            "boxWidth": [10, 42],
        }]
        return out

    # ---- candlestick ----------------------------------------------------------
    if t == "candlestick":
        rows = spec.get("ohlc") or []
        out = cartesian({**cat_axis, "data": [r["date"] for r in rows]}, {**val_axis, "name": (spec.get("yAxis") or {}).get("label"), "scale": True})
        out["series"] = [{
            "type": "candlestick",
            "data": [[r["open"], r["close"], r["low"], r["high"]] for r in rows],
            "itemStyle": {"color": SERIES[2], "color0": SERIES[7], "borderColor": SERIES[2], "borderColor0": SERIES[7], "borderWidth": 1},
        }]
        return out

    # ---- calendar -------------------------------------------------------------
    if t == "calendar":
        days = spec.get("calendar") or []
        vals = [d["value"] for d in days]
        year = (days[0]["date"][:4] if days else "2026")
        return {
            **base, "grid": None,
            "visualMap": {"min": min([0] + vals), "max": max([1] + vals), "orient": "horizontal", "left": "center", "bottom": 2, "itemWidth": 9, "itemHeight": 50, "inRange": {"color": SEQUENTIAL}, "textStyle": {"color": INK["muted"], "fontSize": MICRO}},
            "calendar": {"top": 24, "left": 34, "right": 12, "cellSize": ["auto", 13], "range": year, "itemStyle": {"color": "transparent", "borderColor": INK["surface"], "borderWidth": 2}, "splitLine": {"show": False}, "yearLabel": {"show": False}, "dayLabel": {"color": INK["muted"], "fontSize": MICRO, "nameMap": "en"}, "monthLabel": {"color": INK["muted"], "fontSize": MICRO, "nameMap": "en"}},
            "series": [{"type": "heatmap", "coordinateSystem": "calendar", "data": [[d["date"], d["value"]] for d in days]}],
        }

    # ---- radar ----------------------------------------------------------------
    if t == "radar":
        return {
            **base,
            "radar": {"indicator": [{"name": clip(14, c)} for c in cats], "center": ["50%", "48%"], "radius": "66%", "axisName": {"color": INK["muted"], "fontSize": MICRO}, "splitLine": {"lineStyle": {"color": INK["grid"]}}, "axisLine": {"lineStyle": {"color": INK["grid"]}}, "splitArea": {"show": False}},
            "series": [{"type": "radar", "data": [{"name": s["name"], "value": s["data"]} for s in series], "areaStyle": {"opacity": 0.14}, "lineStyle": {"width": 2}, "symbolSize": 4}],
        }

    # ---- parallel -------------------------------------------------------------
    if t == "parallel":
        axes = []
        for i, c in enumerate(cats):
            col = [(s["data"][i] if i < len(s["data"]) else 0) or 0 for s in series]
            axes.append({"dim": i, "name": clip(11, c), "min": min(col + [0]), "max": max(col + [1]), "nameLocation": "end", "nameGap": 12, "nameTextStyle": {"color": INK["secondary"], "fontSize": MICRO, "fontFamily": FONT}, "axisLine": {"lineStyle": {"color": INK["axis"]}}, "axisTick": {"show": False}, "axisLabel": {"color": INK["muted"], "fontSize": MICRO}})
        return {
            **base,
            "parallelAxis": axes,
            "parallel": {"left": 38, "right": 38, "top": 34, "bottom": bottom_gap + 12},
            "series": [{"type": "parallel", "name": s["name"], "data": [s["data"]], "smooth": True, "lineStyle": {"width": 2, "opacity": 0.75}, "emphasis": {"lineStyle": {"width": 3, "opacity": 1}}} for s in series],
        }

    # ---- heatmap --------------------------------------------------------------
    if t == "heatmap":
        data = []
        for y, row in enumerate(series):
            for x, v in enumerate(row["data"]):
                data.append([x, y, v or 0])
        vals = [d[2] for d in data]
        return {
            **base,
            "grid": {"left": 6, "right": 14, "top": 10, "bottom": 50, "containLabel": True},
            "xAxis": {**cat_axis, "data": cats, "splitArea": {"show": False}},
            "yAxis": {**cat_axis, "data": [clip(16, s["name"]) for s in series], "splitArea": {"show": False}},
            "visualMap": {"min": min(vals + [0]), "max": max(vals + [1]), "orient": "horizontal", "left": "center", "bottom": 2, "itemWidth": 9, "itemHeight": 54, "inRange": {"color": SEQUENTIAL}, "textStyle": {"color": INK["muted"], "fontSize": MICRO}},
            "series": [{"type": "heatmap", "data": data, "itemStyle": {"borderColor": INK["surface"], "borderWidth": 2, "borderRadius": 2}, "label": {"show": len(data) <= 48, "color": INK["primary"], "fontSize": MICRO}}],
        }

    raise ValueError(f"unknown chartType {t!r}")


def _prune_none(node: Any) -> Any:
    """Drop None values so the emitted option is clean JSON (ECharts treats
    absent and null the same, but null keys add noise)."""
    if isinstance(node, dict):
        return {k: _prune_none(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_prune_none(v) for v in node]
    return node


def compile_spec(spec: Any) -> dict[str, Any]:
    """Validate + compile a chart spec (raw dict or ChartSpec) to a clean
    EChartsOption. Raises on a spec that cannot render, which is how the chart
    tools reject bad input before it reaches the canvas."""
    validated = ChartSpec.model_validate(spec).model_dump(mode="json", by_alias=True, exclude_none=True)
    return _prune_none(build_option(validated))


class _EchartsToolInput(BaseModel):
    spec: ChartSpec


# The echarts implementation, as a LangChain tool. Invoked (via .invoke) by the
# chart create/update handlers to compile-and-validate a spec at write time.
build_echarts_option = StructuredTool.from_function(
    func=compile_spec,
    name="build_echarts_option",
    description=(
        "Compile a typed chart spec into an ECharts option object. Returns the "
        "renderer configuration; raises if the spec cannot be drawn."
    ),
    args_schema=_EchartsToolInput,
)

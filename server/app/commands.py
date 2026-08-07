"""The deterministic command layer. The agent proposes typed operations; only
this module validates, places, applies, versions and audits them (plan §2.3)."""

from typing import Any, Iterable, Literal, TypedDict

import asyncpg

from .db import audit, pool, uid
from .specs import StyleSpec, validate_spec

GRID_COLS = 12

# Row heights in grid units. One unit is 40px, so a kpi band is 120px.
HEIGHTS = {"label": 1, "kpi": 4, "short": 6, "standard": 8, "tall": 11}
HeightName = Literal["label", "kpi", "short", "standard", "tall"]

DEFAULT_SIZE: dict[str, dict[str, int]] = {
    "chart": {"w": 6, "h": HEIGHTS["standard"]},
    "kpi": {"w": 3, "h": HEIGHTS["kpi"]},
    "table": {"w": 6, "h": HEIGHTS["standard"]},
    "narrative": {"w": 4, "h": HEIGHTS["standard"]},
    "image": {"w": 4, "h": HEIGHTS["standard"]},
    "control": {"w": 12, "h": 2},
    "label": {"w": 12, "h": 1},
    "statement": {"w": 5, "h": HEIGHTS["tall"]},
    "hero": {"w": 12, "h": HEIGHTS["kpi"]},
}

# Forms that need width or breathing room when the agent does not say.
CHART_SIZE: dict[str, dict[str, int]] = {
    "gauge": {"w": 3, "h": HEIGHTS["short"]},
    "pie": {"w": 4, "h": HEIGHTS["standard"]},
    "donut": {"w": 4, "h": HEIGHTS["standard"]},
    "rose": {"w": 4, "h": HEIGHTS["standard"]},
    "funnel": {"w": 4, "h": HEIGHTS["standard"]},
    "radar": {"w": 4, "h": HEIGHTS["standard"]},
    "sunburst": {"w": 5, "h": HEIGHTS["tall"]},
    "treemap": {"w": 6, "h": HEIGHTS["standard"]},
    "tree": {"w": 6, "h": HEIGHTS["standard"]},
    "sankey": {"w": 8, "h": HEIGHTS["tall"]},
    "chord": {"w": 5, "h": HEIGHTS["tall"]},
    "graph": {"w": 5, "h": HEIGHTS["tall"]},
    "calendar": {"w": 12, "h": HEIGHTS["short"]},
    "heatmap": {"w": 7, "h": HEIGHTS["standard"]},
    "parallel": {"w": 8, "h": HEIGHTS["standard"]},
    "theme_river": {"w": 8, "h": HEIGHTS["standard"]},
    "candlestick": {"w": 8, "h": HEIGHTS["standard"]},
    "boxplot": {"w": 6, "h": HEIGHTS["standard"]},
}


class ApplyResult(TypedDict):
    changeSetId: str
    applied: list[dict[str, Any]]
    errors: list[str]


# ---- fact binding -----------------------------------------------------------
# A binding maps a spec path to a fact id. The command layer resolves the fact's
# stored value INTO the spec here, so the model's typed number is discarded and the
# fact is the source of record — the structural half of "the LLM does not inject data".

def _norm_path(path: str) -> list[str]:
    return [p for p in path.replace("[", ".").replace("]", ".").split(".") if p != ""]


def _get_at(obj: Any, parts: list[str]) -> Any:
    cur = obj
    for p in parts:
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if p not in cur:
                return None
            cur = cur[p]
        else:
            return None
    return cur


def _set_at(obj: Any, parts: list[str], value: Any) -> None:
    cur = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _numeric_leaves(kind: str, spec: dict[str, Any]) -> list[list[str]]:
    """Every data-bearing number in a spec — the values that, if 'measured', need a
    source. Editorial marks (annotation values) and UI-derived shares (statement
    percent) are deliberately excluded."""
    out: list[list[str]] = []

    def num(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    if kind == "kpi":
        if num(spec.get("value")):
            out.append(["value"])
        comp = spec.get("comparison")
        if isinstance(comp, dict) and num(comp.get("baseline")):
            out.append(["comparison", "baseline"])
        for j, v in enumerate(spec.get("sparkline") or []):
            if num(v):
                out.append(["sparkline", str(j)])
    elif kind == "chart":
        for i, s in enumerate(spec.get("series") or []):
            for j, v in enumerate((s or {}).get("data") or []):
                if num(v):
                    out.append(["series", str(i), "data", str(j)])
        for j, v in enumerate(spec.get("target") or []):
            if num(v):
                out.append(["target", str(j)])
        for i, l in enumerate(spec.get("links") or []):
            if num((l or {}).get("value")):
                out.append(["links", str(i), "value"])
        for i, h in enumerate(spec.get("hierarchy") or []):
            if num((h or {}).get("value")):
                out.append(["hierarchy", str(i), "value"])
        for i, o in enumerate(spec.get("ohlc") or []):
            for k in ("open", "high", "low", "close"):
                if num((o or {}).get(k)):
                    out.append(["ohlc", str(i), k])
        for i, b in enumerate(spec.get("boxes") or []):
            for k in ("min", "q1", "median", "q3", "max"):
                if num((b or {}).get(k)):
                    out.append(["boxes", str(i), k])
        for i, c in enumerate(spec.get("calendar") or []):
            if num((c or {}).get("value")):
                out.append(["calendar", str(i), "value"])
    elif kind == "statement":
        for i, l in enumerate(spec.get("lines") or []):
            if num((l or {}).get("value")):
                out.append(["lines", str(i), "value"])
    elif kind == "table":
        for i, row in enumerate(spec.get("rows") or []):
            for k, v in (row or {}).items():
                if num(v):
                    out.append(["rows", str(i), k])
    return out


def _covered(leaf: list[str], bound: list[list[str]]) -> bool:
    return any(leaf[: len(bp)] == bp for bp in bound)


async def _facts_by_id(conn, canvas_id, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {}
    try:
        uids = [uid(i) for i in ids]
    except Exception:  # noqa: BLE001
        return {}
    rows = await conn.fetch(
        "SELECT id, kind, value, points FROM canvas.facts WHERE canvas_id = $1 AND id = ANY($2::uuid[])",
        uid(canvas_id),
        uids,
    )
    return {str(r["id"]): r for r in rows}


async def bind_and_materialize(
    conn,
    canvas_id,
    kind: str,
    spec: dict[str, Any],
    bindings: list[dict[str, Any]] | None,
    provenance: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve each binding's fact value into the spec, then — if the widget claims
    'measured' — require every number to be backed by a fact. Returns (spec, error)."""
    bindings = bindings or []
    facts = await _facts_by_id(conn, canvas_id, [b.get("factId") for b in bindings if b.get("factId")])
    bound: list[list[str]] = []
    for b in bindings:
        fid, path = b.get("factId"), b.get("path")
        if not fid or not path:
            return None, "each binding needs a path and a factId"
        fact = facts.get(str(fid))
        if fact is None:
            return None, f"binding fact {fid} is not a fact on this canvas"
        parts = _norm_path(path)
        if fact["kind"] == "series":
            points = fact["points"] or []
            ys = [p.get("y") for p in points]
            target = _get_at(spec, parts)
            if isinstance(target, dict) and "data" in target:
                target["data"] = ys
                xaxis = spec.get("xAxis") or {}
                if not xaxis.get("categories"):
                    spec.setdefault("xAxis", {})
                    spec["xAxis"]["categories"] = [p.get("x") for p in points]
            elif isinstance(target, list) or target is None:
                _set_at(spec, parts, ys)
            else:
                return None, f'series binding path "{path}" must point at a series or an array'
        else:
            if _get_at(spec, parts) is None:
                return None, f'binding path "{path}" does not resolve in the spec'
            _set_at(spec, parts, fact["value"])
        bound.append(parts)

    if (provenance or {}).get("confidence") == "measured":
        for leaf in _numeric_leaves(kind, spec):
            if not _covered(leaf, bound):
                return None, (
                    f'"{".".join(leaf)}" is labelled measured but is not bound to a fact — '
                    "web_search for it and bind it, or lower the widget's confidence."
                )
    return spec, None


def size_for(kind: str, spec: Any) -> dict[str, int]:
    if kind == "chart":
        chart_type = (spec or {}).get("chartType")
        if chart_type and chart_type in CHART_SIZE:
            return CHART_SIZE[chart_type]
    return DEFAULT_SIZE[kind]


async def _next_slot(conn: asyncpg.Connection, canvas_id, w: int, h: int) -> tuple[int, int]:
    """Next free row-major slot in a 12-col grid — placements are server-generated (plan §2.10)."""
    rows = await conn.fetch(
        "SELECT x, y, w, h FROM canvas.placements WHERE canvas_id = $1", canvas_id
    )

    def taken(x: int, y: int) -> bool:
        return any(
            x < p["x"] + p["w"] and x + w > p["x"] and y < p["y"] + p["h"] and y + h > p["y"]
            for p in rows
        )

    for y in range(200):
        for x in range(GRID_COLS - w + 1):
            if not taken(x, y):
                return x, y
    return 0, 200


async def _snapshot(conn: asyncpg.Connection, widget_id, change_set_id) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT kind, title, spec, provenance, current_version FROM canvas.widgets WHERE id = $1",
        widget_id,
    )
    if row is None:
        return None
    w = dict(row)
    await conn.execute(
        """INSERT INTO canvas.versions (entity_type, entity_id, version_number, definition, change_set_id)
           VALUES ('widget', $1, $2, $3, $4)
           ON CONFLICT (entity_type, entity_id, version_number) DO NOTHING""",
        widget_id,
        w["current_version"],
        w,
        change_set_id,
    )
    return w


async def apply_change_set(
    canvas_id: Any, operations: Iterable[dict[str, Any]], origin: str
) -> ApplyResult:
    """Validate then apply a typed change set in one transaction. The agent never writes storage directly."""
    cid = uid(canvas_id)
    operations = list(operations)
    errors: list[str] = []
    applied: list[dict[str, Any]] = []
    inverse: list[dict[str, Any]] = []

    async with pool().acquire() as conn:
        try:
            async with conn.transaction():
                change_set_id = await conn.fetchval(
                    """INSERT INTO canvas.change_sets (canvas_id, origin, status, operations)
                       VALUES ($1, $2, 'validating', $3) RETURNING id""",
                    cid,
                    origin,
                    operations,
                )

                for op in operations:
                    kind = op.get("kind")

                    if kind == "add_widget":
                        widget_kind = op.get("widgetKind")
                        spec, error = validate_spec(widget_kind, op.get("spec"))
                        if error:
                            errors.append(f'add_widget "{op.get("title")}": {error}')
                            continue
                        bindings = op.get("bindings")
                        spec, bind_error = await bind_and_materialize(
                            conn, cid, widget_kind, spec, bindings, op.get("provenance")
                        )
                        if bind_error:
                            errors.append(f'add_widget "{op.get("title")}": {bind_error}')
                            continue
                        size = op.get("size") or size_for(widget_kind, spec)
                        x, y = await _next_slot(conn, cid, size["w"], size["h"])
                        widget_id = await conn.fetchval(
                            """INSERT INTO canvas.widgets (canvas_id, kind, title, spec, provenance, bindings)
                               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
                            cid,
                            widget_kind,
                            op.get("title"),
                            spec,
                            op.get("provenance"),
                            bindings,
                        )
                        await conn.execute(
                            """INSERT INTO canvas.placements (canvas_id, widget_id, x, y, w, h)
                               VALUES ($1, $2, $3, $4, $5, $6)""",
                            cid, widget_id, x, y, size["w"], size["h"],
                        )
                        await conn.execute(
                            """INSERT INTO canvas.versions (entity_type, entity_id, version_number, definition, change_set_id)
                               VALUES ('widget', $1, 1, $2, $3)""",
                            widget_id,
                            {"kind": widget_kind, "title": op.get("title"), "spec": spec},
                            change_set_id,
                        )
                        applied.append({"operation": op, "widgetId": str(widget_id)})
                        inverse.append({"kind": "remove_widget", "widgetId": str(widget_id)})

                    elif kind == "update_widget":
                        try:
                            wid = uid(op.get("widgetId"))
                        except (ValueError, AttributeError, TypeError):
                            errors.append(f'update_widget: {op.get("widgetId")} is not a widget id')
                            continue
                        prev = await _snapshot(conn, wid, change_set_id)
                        if prev is None:
                            errors.append(f'update_widget: {op.get("widgetId")} not found')
                            continue
                        spec = prev["spec"]
                        bindings = op.get("bindings")
                        if op.get("spec") is not None:
                            spec, error = validate_spec(prev["kind"], op["spec"])
                            if error:
                                errors.append(f'update_widget {op["widgetId"]}: invalid spec')
                                continue
                            spec, bind_error = await bind_and_materialize(
                                conn, cid, prev["kind"], spec, bindings,
                                op.get("provenance") or prev.get("provenance"),
                            )
                            if bind_error:
                                errors.append(f'update_widget {op["widgetId"]}: {bind_error}')
                                continue
                        await conn.execute(
                            """UPDATE canvas.widgets
                               SET title = $2, spec = $3, provenance = COALESCE($4, provenance),
                                   bindings = COALESCE($5, bindings),
                                   current_version = current_version + 1, updated_at = now()
                               WHERE id = $1""",
                            wid,
                            op.get("title") or prev["title"],
                            spec,
                            op.get("provenance"),
                            bindings,
                        )
                        applied.append({"operation": op, "widgetId": str(wid)})
                        inverse.append(
                            {
                                "kind": "update_widget",
                                "widgetId": str(wid),
                                "title": prev["title"],
                                "spec": prev["spec"],
                            }
                        )

                    elif kind == "remove_widget":
                        try:
                            wid = uid(op.get("widgetId"))
                        except (ValueError, AttributeError, TypeError):
                            errors.append(f'remove_widget: {op.get("widgetId")} is not a widget id')
                            continue
                        prev = await _snapshot(conn, wid, change_set_id)
                        if prev is None:
                            errors.append(f'remove_widget: {op.get("widgetId")} not found')
                            continue
                        await conn.execute(
                            "UPDATE canvas.widgets SET status = 'trashed' WHERE id = $1", wid
                        )
                        await conn.execute(
                            "DELETE FROM canvas.placements WHERE widget_id = $1", wid
                        )
                        applied.append({"operation": op, "widgetId": str(wid)})
                        inverse.append(
                            {
                                "kind": "add_widget",
                                "widgetKind": prev["kind"],
                                "title": prev["title"],
                                "spec": prev["spec"],
                                "provenance": prev["provenance"],
                            }
                        )

                    elif kind == "set_style":
                        # Canvas identity is a first-class edit: goes through the change-set
                        # machinery so it version-bumps, audits, and undoes like anything else.
                        try:
                            next_style = StyleSpec.model_validate(op.get("style")).model_dump(
                                mode="json", exclude_none=True
                            )
                        except Exception as e:
                            errors.append(f"set_style: {e}")
                            continue
                        prev_style = await conn.fetchval(
                            "SELECT style FROM canvas.canvases WHERE id = $1", cid
                        )
                        await conn.execute(
                            "UPDATE canvas.canvases SET style = $2 WHERE id = $1",
                            cid, next_style,
                        )
                        applied.append({"operation": op, "widgetId": None})
                        inverse.append({"kind": "set_style", "style": prev_style})

                    elif kind == "add_chart_series":
                        # Surgical: append new series to an existing chart without needing to
                        # know the current series data. Preserves everything else on the widget.
                        try:
                            wid = uid(op.get("widgetId"))
                        except (ValueError, AttributeError, TypeError):
                            errors.append(f'add_chart_series: {op.get("widgetId")} is not a widget id')
                            continue
                        prev = await _snapshot(conn, wid, change_set_id)
                        if prev is None:
                            errors.append(f'add_chart_series: {op.get("widgetId")} not found')
                            continue
                        if prev["kind"] != "chart":
                            errors.append(f'add_chart_series: {op.get("widgetId")} is not a chart')
                            continue
                        new_series = op.get("series") or []
                        if not isinstance(new_series, list) or not new_series:
                            errors.append("add_chart_series: series[] required")
                            continue
                        prev_spec = dict(prev["spec"])
                        added_names = [s.get("name") for s in new_series]
                        merged_series = list(prev_spec.get("series") or [])
                        # Skip names that already exist — an idempotent merge, not a duplicator.
                        # factIds ride alongside series so a bound fact survives the filter.
                        fact_ids = op.get("factIds") or []
                        existing = {s.get("name") for s in merged_series}
                        pairs = [
                            (s, fact_ids[i] if i < len(fact_ids) else None)
                            for i, s in enumerate(new_series)
                            if s.get("name") not in existing
                        ]
                        if not pairs:
                            errors.append(
                                f'add_chart_series: series {added_names} already on the chart'
                            )
                            continue
                        appended = [s for s, _ in pairs]
                        base = len(merged_series)
                        prev_spec["series"] = merged_series + appended
                        spec, error = validate_spec("chart", prev_spec)
                        if error:
                            errors.append(f'add_chart_series: {error}')
                            continue
                        new_bindings = [
                            {"path": f"series.{base + i}", "factId": f}
                            for i, (_, f) in enumerate(pairs)
                            if f
                        ]
                        if new_bindings:
                            spec, bind_error = await bind_and_materialize(
                                conn, cid, "chart", spec, new_bindings, None
                            )
                            if bind_error:
                                errors.append(f"add_chart_series: {bind_error}")
                                continue
                        prior_bindings = (
                            await conn.fetchval("SELECT bindings FROM canvas.widgets WHERE id = $1", wid)
                            or []
                        )
                        await conn.execute(
                            """UPDATE canvas.widgets
                               SET spec = $2, bindings = $3,
                                   current_version = current_version + 1, updated_at = now()
                               WHERE id = $1""",
                            wid, spec, prior_bindings + new_bindings,
                        )
                        applied.append({"operation": op, "widgetId": str(wid)})
                        inverse.append(
                            {
                                "kind": "remove_chart_series",
                                "widgetId": str(wid),
                                "names": [s.get("name") for s in appended],
                            }
                        )

                    elif kind == "remove_chart_series":
                        try:
                            wid = uid(op.get("widgetId"))
                        except (ValueError, AttributeError, TypeError):
                            errors.append(f'remove_chart_series: {op.get("widgetId")} is not a widget id')
                            continue
                        prev = await _snapshot(conn, wid, change_set_id)
                        if prev is None:
                            errors.append(f'remove_chart_series: {op.get("widgetId")} not found')
                            continue
                        if prev["kind"] != "chart":
                            errors.append(f'remove_chart_series: {op.get("widgetId")} is not a chart')
                            continue
                        names = op.get("names") or []
                        if not isinstance(names, list) or not names:
                            errors.append("remove_chart_series: names[] required")
                            continue
                        prev_spec = dict(prev["spec"])
                        drop = set(names)
                        removed = [s for s in (prev_spec.get("series") or []) if s.get("name") in drop]
                        if not removed:
                            errors.append(
                                f'remove_chart_series: none of {names} match existing series'
                            )
                            continue
                        prev_spec["series"] = [
                            s for s in (prev_spec.get("series") or []) if s.get("name") not in drop
                        ]
                        spec, error = validate_spec("chart", prev_spec)
                        if error:
                            errors.append(f'remove_chart_series: {error}')
                            continue
                        await conn.execute(
                            """UPDATE canvas.widgets
                               SET spec = $2, current_version = current_version + 1, updated_at = now()
                               WHERE id = $1""",
                            wid, spec,
                        )
                        applied.append({"operation": op, "widgetId": str(wid)})
                        inverse.append(
                            {"kind": "add_chart_series", "widgetId": str(wid), "series": removed}
                        )

                    elif kind == "set_chart_type":
                        # Swap a chart's form while preserving its data, axes and
                        # annotations — the "make that a bar chart" edit.
                        try:
                            wid = uid(op.get("widgetId"))
                        except (ValueError, AttributeError, TypeError):
                            errors.append(f'set_chart_type: {op.get("widgetId")} is not a widget id')
                            continue
                        prev = await _snapshot(conn, wid, change_set_id)
                        if prev is None:
                            errors.append(f'set_chart_type: {op.get("widgetId")} not found')
                            continue
                        if prev["kind"] != "chart":
                            errors.append(f'set_chart_type: {op.get("widgetId")} is not a chart')
                            continue
                        prev_spec = dict(prev["spec"])
                        prev_type = prev_spec.get("chartType")
                        prev_spec["chartType"] = op.get("chartType")
                        spec, error = validate_spec("chart", prev_spec)
                        if error:
                            errors.append(f"set_chart_type: {error}")
                            continue
                        await conn.execute(
                            """UPDATE canvas.widgets
                               SET spec = $2, current_version = current_version + 1, updated_at = now()
                               WHERE id = $1""",
                            wid, spec,
                        )
                        applied.append({"operation": op, "widgetId": str(wid)})
                        inverse.append(
                            {"kind": "set_chart_type", "widgetId": str(wid), "chartType": prev_type}
                        )

                    elif kind == "set_chart_annotations":
                        # Replace a chart's on-plot marks, keeping the data intact.
                        try:
                            wid = uid(op.get("widgetId"))
                        except (ValueError, AttributeError, TypeError):
                            errors.append(f'set_chart_annotations: {op.get("widgetId")} is not a widget id')
                            continue
                        prev = await _snapshot(conn, wid, change_set_id)
                        if prev is None:
                            errors.append(f'set_chart_annotations: {op.get("widgetId")} not found')
                            continue
                        if prev["kind"] != "chart":
                            errors.append(f'set_chart_annotations: {op.get("widgetId")} is not a chart')
                            continue
                        prev_spec = dict(prev["spec"])
                        prev_annotations = prev_spec.get("annotations")
                        prev_spec["annotations"] = op.get("annotations") or []
                        spec, error = validate_spec("chart", prev_spec)
                        if error:
                            errors.append(f"set_chart_annotations: {error}")
                            continue
                        await conn.execute(
                            """UPDATE canvas.widgets
                               SET spec = $2, current_version = current_version + 1, updated_at = now()
                               WHERE id = $1""",
                            wid, spec,
                        )
                        applied.append({"operation": op, "widgetId": str(wid)})
                        inverse.append(
                            {
                                "kind": "set_chart_annotations",
                                "widgetId": str(wid),
                                "annotations": prev_annotations or [],
                            }
                        )

                    elif kind in ("move_widget", "resize_widget"):
                        try:
                            wid = uid(op.get("widgetId"))
                        except (ValueError, AttributeError, TypeError):
                            errors.append(f'{kind}: {op.get("widgetId")} is not a widget id')
                            continue
                        p = await conn.fetchrow(
                            "SELECT x, y, w, h FROM canvas.placements WHERE widget_id = $1", wid
                        )
                        if p is None:
                            errors.append(f'{kind}: placement for {op.get("widgetId")} not found')
                            continue
                        if kind == "move_widget":
                            x = max(0, min(GRID_COLS - p["w"], int(op.get("x", 0))))
                            await conn.execute(
                                "UPDATE canvas.placements SET x = $2, y = $3 WHERE widget_id = $1",
                                wid, x, max(0, int(op.get("y", 0))),
                            )
                            inverse.append(
                                {"kind": "move_widget", "widgetId": str(wid), "x": p["x"], "y": p["y"]}
                            )
                        else:
                            w = max(2, min(GRID_COLS, int(op.get("w", 2))))
                            await conn.execute(
                                "UPDATE canvas.placements SET w = $2, h = $3 WHERE widget_id = $1",
                                wid, w, max(2, int(op.get("h", 2))),
                            )
                            inverse.append(
                                {"kind": "resize_widget", "widgetId": str(wid), "w": p["w"], "h": p["h"]}
                            )
                        applied.append({"operation": op, "widgetId": str(wid)})

                    else:
                        errors.append(f"unknown operation {kind}")

                inverse.reverse()
                await conn.execute(
                    "UPDATE canvas.change_sets SET status = $2, inverse = $3 WHERE id = $1",
                    change_set_id,
                    "rejected" if errors and not applied else "applied",
                    inverse,
                )
                await conn.execute(
                    """UPDATE canvas.canvases SET current_version = current_version + 1,
                       updated_at = now() WHERE id = $1""",
                    cid,
                )
        except Exception as e:
            await audit("apply_change_set", "failed", "canvas", cid, {"error": str(e)})
            raise

    await audit(
        "apply_change_set",
        "partial" if errors else "applied",
        "canvas",
        cid,
        {
            "changeSetId": str(change_set_id),
            "opCount": len(operations),
            "appliedCount": len(applied),
            "errors": errors,
        },
    )
    return {"changeSetId": str(change_set_id), "applied": applied, "errors": errors}


async def apply_layout(canvas_id: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Row-based layout: every card in a row shares one height and the spans are
    normalised to fill 12 columns. This is what makes a canvas read as a
    dashboard rather than a pile of boxes."""
    cid = uid(canvas_id)
    existing = await pool().fetch(
        "SELECT widget_id FROM canvas.placements WHERE canvas_id = $1", cid
    )
    known = {r["widget_id"] for r in existing}
    seen: set = set()
    errors: list[str] = []
    y = 0

    async with pool().acquire() as conn:
        async with conn.transaction():
            for row in rows:
                items = []
                for item in row.get("items", []):
                    try:
                        wid = uid(item.get("widgetId"))
                    except (ValueError, AttributeError, TypeError):
                        errors.append(f'unknown widget {item.get("widgetId")}')
                        continue
                    if wid not in known:
                        errors.append(f'unknown widget {item.get("widgetId")}')
                        continue
                    if wid in seen:
                        continue
                    items.append((wid, max(1, int(item.get("span", 1)))))
                if not items:
                    continue

                h = HEIGHTS.get(row.get("height"), HEIGHTS["standard"])
                total = sum(span for _, span in items)
                x = 0
                for idx, (wid, span) in enumerate(items):
                    # Scale the row to exactly 12 columns, giving any remainder to the last card.
                    w = max(2, round(span / total * GRID_COLS))
                    if idx == len(items) - 1:
                        w = max(2, GRID_COLS - x)
                    w = min(w, GRID_COLS - x)
                    if w <= 0:
                        continue
                    seen.add(wid)
                    await conn.execute(
                        "UPDATE canvas.placements SET x = $2, y = $3, w = $4, h = $5 WHERE widget_id = $1",
                        wid, x, y, w, h,
                    )
                    x += w
                y += h

            # Anything the agent left out keeps its size and stacks below.
            for wid in known - seen:
                await conn.execute(
                    "UPDATE canvas.placements SET x = 0, y = $2 WHERE widget_id = $1", wid, y
                )
                y += HEIGHTS["standard"]

    await audit(
        "apply_layout",
        "partial" if errors else "applied",
        "canvas",
        cid,
        {"rows": len(rows), "errors": errors},
    )
    return {"rows": len(rows), "placed": len(seen), "errors": errors}


async def apply_lanes(canvas_id: Any, lanes: list[dict[str, Any]]) -> dict[str, Any]:
    """Lanes are vertical stacks side by side — the shape a flow or pipeline needs."""
    cid = uid(canvas_id)
    existing = await pool().fetch(
        "SELECT widget_id FROM canvas.placements WHERE canvas_id = $1", cid
    )
    known = {r["widget_id"] for r in existing}
    seen: set = set()
    errors: list[str] = []

    total = sum(max(1, int(lane.get("span", 1))) for lane in lanes) or 1
    x = 0
    max_y = 0

    async with pool().acquire() as conn:
        async with conn.transaction():
            for idx, lane in enumerate(lanes):
                w = max(2, round(max(1, int(lane.get("span", 1))) / total * GRID_COLS))
                if idx == len(lanes) - 1:
                    w = max(2, GRID_COLS - x)
                w = min(w, GRID_COLS - x)
                if w <= 0:
                    continue

                y = 0
                for item in lane.get("items", []):
                    try:
                        wid = uid(item.get("widgetId"))
                    except (ValueError, AttributeError, TypeError):
                        errors.append(f'unknown widget {item.get("widgetId")}')
                        continue
                    if wid not in known:
                        errors.append(f'unknown widget {item.get("widgetId")}')
                        continue
                    if wid in seen:
                        continue
                    h = HEIGHTS.get(item.get("height"), HEIGHTS["standard"])
                    seen.add(wid)
                    await conn.execute(
                        "UPDATE canvas.placements SET x = $2, y = $3, w = $4, h = $5 WHERE widget_id = $1",
                        wid, x, y, w, h,
                    )
                    y += h
                max_y = max(max_y, y)
                x += w

            for wid in known - seen:
                await conn.execute(
                    "UPDATE canvas.placements SET x = 0, y = $2, w = 12 WHERE widget_id = $1",
                    wid, max_y,
                )
                max_y += HEIGHTS["standard"]

    await audit(
        "apply_lanes",
        "partial" if errors else "applied",
        "canvas",
        cid,
        {"lanes": len(lanes), "errors": errors},
    )
    return {"lanes": len(lanes), "placed": len(seen), "errors": errors}


async def compact(canvas_id: Any) -> int:
    """The agent can request overlapping placements. Sweep row-major and push each
    widget down to the first free slot at or below its requested row."""
    cid = uid(canvas_id)
    rows = await pool().fetch(
        """SELECT widget_id, x, y, w, h FROM canvas.placements
           WHERE canvas_id = $1 ORDER BY y, x""",
        cid,
    )
    placed: list[dict[str, int]] = []
    updates: list[tuple[Any, int]] = []

    for p in rows:
        w = min(GRID_COLS, max(1, p["w"]))
        x = max(0, min(GRID_COLS - w, p["x"]))
        y = max(0, p["y"])

        def hits(ty: int, x=x, w=w, h=p["h"]) -> bool:
            return any(
                x < o["x"] + o["w"] and x + w > o["x"] and ty < o["y"] + o["h"] and ty + h > o["y"]
                for o in placed
            )

        while hits(y):
            y += 1
        placed.append({"x": x, "y": y, "w": w, "h": p["h"]})
        if y != p["y"] or x != p["x"]:
            updates.append((p["widget_id"], y))

    for wid, y in updates:
        await pool().execute(
            "UPDATE canvas.placements SET y = $2 WHERE widget_id = $1", wid, y
        )
    return len(updates)


async def normalize_rows(canvas_id: Any) -> int:
    """Snap ragged placements into clean rows: overlapping bands merge, each row
    takes one height, widths refill 12. Runs when a turn adds widgets without
    ever calling a layout tool."""
    cid = uid(canvas_id)
    rows = await pool().fetch(
        "SELECT widget_id, x, y, w, h FROM canvas.placements WHERE canvas_id = $1 ORDER BY y, x",
        cid,
    )
    if not rows:
        return 0

    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_end = -1
    for r in rows:
        p = dict(r)
        if not cur or p["y"] < cur_end:
            cur.append(p)
            cur_end = max(cur_end, p["y"] + p["h"])
        else:
            groups.append(cur)
            cur = [p]
            cur_end = p["y"] + p["h"]
    groups.append(cur)

    y = 0
    changed = 0
    for g in groups:
        g.sort(key=lambda p: p["x"])
        h = max(p["h"] for p in g)
        total = sum(max(1, p["w"]) for p in g)
        x = 0
        for i, p in enumerate(g):
            w = max(2, round(max(1, p["w"]) / total * GRID_COLS))
            if i == len(g) - 1:
                w = max(2, GRID_COLS - x)
            w = min(w, GRID_COLS - x)
            if w <= 0:
                continue
            if (p["x"], p["y"], p["w"], p["h"]) != (x, y, w, h):
                await pool().execute(
                    "UPDATE canvas.placements SET x=$2, y=$3, w=$4, h=$5 WHERE widget_id=$1",
                    p["widget_id"], x, y, w, h,
                )
                changed += 1
            x += w
        y += h

    if changed:
        await audit("normalize_rows", "applied", "canvas", cid, {"changed": changed})
    return changed


async def undo_last(canvas_id: Any) -> ApplyResult | None:
    """Undo is a new change set of stored inverse operations — history is never deleted (plan §2.8).

    That new change set is itself applied and carries its own inverse, so it must never
    become the *next* undo's target: otherwise a second consecutive undo would just redo
    what the first one removed. Undo targets real edits only (`origin <> 'undo'`), which
    is what lets repeated undo walk back through the stack.
    """
    cid = uid(canvas_id)
    row = await pool().fetchrow(
        """SELECT id, inverse FROM canvas.change_sets
           WHERE canvas_id = $1 AND status = 'applied' AND undone = false
             AND inverse IS NOT NULL AND origin <> 'undo'
           ORDER BY created_at DESC LIMIT 1""",
        cid,
    )
    if row is None:
        return None
    result = await apply_change_set(cid, row["inverse"], "undo")
    await pool().execute(
        "UPDATE canvas.change_sets SET undone = true WHERE id = $1", row["id"]
    )
    return result

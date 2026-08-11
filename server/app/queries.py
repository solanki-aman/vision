"""The data layer: recipes that can be run again, and the runs that produced facts.

Before this module a fact was an orphan — it recorded that a search returned `455.0`
on a Tuesday, and nothing in the row could produce Wednesday's number or *a different
viewer's* number. That is fine for a canvas, which answers one question once. It is
not enough for a pinned tile, which has to refresh, and it is not enough for a shared
tile, which has to resolve per viewer.

So a fact becomes the output of a run, and a run is one execution of a query:

    query  — the recipe: source, op, params, access_class     (durable, re-executable)
    run    — one execution, as a named principal, at a time   (append-only)
    fact   — an observation the run produced                  (unchanged shape)

`widget.spec` keeps its numbers, but they are demoted to a cache of the last
materialisation. Truth for any bound leaf is binding → query → latest run → fact, and
`materialize()` here is what turns that chain back into a spec for whoever is looking.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from . import finance
from .commands import _get_at, _norm_path, _set_at
from .db import pool, uid

log = logging.getLogger("vision.queries")

# Derived from the source, never chosen by a user or the model. `compute` resolves to
# the strictest class among its inputs, so arithmetic cannot launder a warehouse figure
# into a public one.
ACCESS_CLASS = {
    "web": "public",
    "warehouse": "entitled",
    "document": "entitled",
    "compute": "derived",
}

# Whether re-running the recipe can produce a different answer. A document is static
# and a tile built only from one is snapshot-only — the UI says so rather than
# offering a daily refresh on a number that physically cannot move.
REFRESHABLE = {"web": True, "warehouse": True, "document": False, "compute": True}


SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas.queries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canvas_id UUID REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  source TEXT NOT NULL,                 -- web | warehouse | document | compute
  access_class TEXT NOT NULL,           -- public | entitled | derived
  op TEXT NOT NULL,                     -- a registered operation name, never SQL
  params JSONB NOT NULL,
  input_query_ids UUID[],
  fingerprint TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (canvas_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_queries_canvas ON canvas.queries (canvas_id);

CREATE TABLE IF NOT EXISTS canvas.query_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  query_id UUID NOT NULL REFERENCES canvas.queries(id) ON DELETE CASCADE,
  ran_as TEXT NOT NULL,
  entitlement_fingerprint TEXT,
  status TEXT NOT NULL,                 -- ok | narrowed | denied | failed
  withheld JSONB,
  error TEXT,
  ran_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_query_runs_query ON canvas.query_runs (query_id, ran_at DESC);
"""


def fingerprint(source: str, op: str, params: Any) -> str:
    body = json.dumps({"s": source, "o": op, "p": params}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


async def access_class_for(source: str, input_query_ids: Sequence[Any] | None) -> str:
    """`compute` inherits the strictest class among its inputs; everything else is fixed."""
    base = ACCESS_CLASS.get(source, "entitled")
    if source != "compute" or not input_query_ids:
        return base
    rows = await pool().fetch(
        "SELECT access_class FROM canvas.queries WHERE id = ANY($1::uuid[])",
        [uid(i) for i in input_query_ids],
    )
    classes = {r["access_class"] for r in rows}
    return "entitled" if "entitled" in classes or "derived" in classes else "public"


async def ensure_query(
    canvas_id: Any,
    *,
    source: str,
    op: str,
    params: dict[str, Any],
    input_query_ids: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Record a recipe, or return the identical one already recorded.

    Deduping on the fingerprint means the same question asked twice on one canvas is
    one query with two runs, which is what makes a delta computable at all.
    """
    fp = fingerprint(source, op, params)
    klass = await access_class_for(source, input_query_ids)
    row = await pool().fetchrow(
        """INSERT INTO canvas.queries
             (canvas_id, source, access_class, op, params, input_query_ids, fingerprint)
           VALUES ($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT (canvas_id, fingerprint) DO UPDATE SET op = EXCLUDED.op
           RETURNING id, canvas_id, source, access_class, op, params, input_query_ids""",
        uid(canvas_id),
        source,
        klass,
        op,
        params,
        [uid(i) for i in input_query_ids] if input_query_ids else None,
        fp,
    )
    return dict(row)


async def get_query(query_id: Any) -> dict[str, Any] | None:
    row = await pool().fetchrow(
        """SELECT id, canvas_id, source, access_class, op, params, input_query_ids
           FROM canvas.queries WHERE id = $1""",
        uid(query_id),
    )
    return dict(row) if row else None


# ---- execution --------------------------------------------------------------------------


@dataclass
class RunResult:
    run_id: str
    status: str                       # ok | narrowed | denied | failed
    facts: list[dict[str, Any]]       # ready for db.record_facts
    withheld: dict[str, int]
    entitlement_fingerprint: str | None
    as_of: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "narrowed")


def _project(rows: list[dict[str, Any]], project: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn warehouse rows into the fact shape the canvas already understands.

    A projection is part of the recipe, not of the result, so re-running a query next
    quarter produces the same *shape* of fact against different numbers — which is what
    lets a binding survive a refresh.
    """
    kind = project.get("kind", "series")
    label = project.get("label") or "value"
    unit = project.get("unit")
    entity = project.get("entity")

    if kind == "series":
        x, y = project.get("x"), project.get("y")
        points = [
            {"x": str(r.get(x)), "y": None if r.get(y) is None else float(r.get(y))}
            for r in rows
        ]
        return [{
            "kind": "series", "label": label, "unit": unit, "entity": entity,
            "points": points, "confidence": "illustrative",
        }]

    # scalar: one number, optionally aggregated across the rows
    y = project.get("y")
    agg = project.get("agg", "sum")
    values = [float(r[y]) for r in rows if r.get(y) is not None]
    if not values:
        value = None
    elif agg == "last":
        value = values[-1]
    elif agg == "avg":
        value = sum(values) / len(values)
    else:
        value = sum(values)
    return [{
        "kind": "scalar", "label": label, "unit": unit, "entity": entity,
        "value": value, "confidence": "illustrative",
    }]


async def run_query(query: dict[str, Any], principals: Sequence[str]) -> RunResult:
    """Execute a recipe as the given principals and record the run.

    The run row is written whatever happens, including a denial — "why is this tile
    empty" is answerable only if the refusal left a trace.
    """
    params = query.get("params") or {}
    source = query["source"]
    status, facts, withheld, ent_fp, as_of, error = "ok", [], {}, None, None, None

    try:
        if source == "warehouse":
            result = await finance.execute(query["op"], params.get("args") or {}, principals)
            ent_fp, withheld, as_of = result.fingerprint, result.withheld, result.as_of
            if not result.rows:
                status = "denied" if result.withheld else "ok"
            elif result.narrowed:
                status = "narrowed"
            facts = _project(result.rows, params.get("project") or {}) if result.rows else []
        else:
            # web / document / compute recipes are recorded and replayable through the
            # agent's own tools; a scheduled refresh only re-executes the warehouse,
            # which is the only source where re-running is both cheap and deterministic.
            status = "ok"
            facts = []
    except finance.UnknownOp:
        status, error = "failed", "unknown operation"
    except Exception as e:  # noqa: BLE001
        log.exception("query %s failed", query.get("id"))
        status, error = "failed", str(e)[:300]

    row = await pool().fetchrow(
        """INSERT INTO canvas.query_runs
             (query_id, ran_as, entitlement_fingerprint, status, withheld, error)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        uid(query["id"]),
        principals[0] if principals else "unknown",
        ent_fp,
        status,
        withheld or None,
        error,
    )
    return RunResult(
        run_id=str(row["id"]),
        status=status,
        facts=facts,
        withheld=withheld,
        entitlement_fingerprint=ent_fp,
        as_of=as_of,
        error=error,
    )


async def record_run_facts(
    canvas_id: Any, query_id: Any, run_id: str, facts: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Persist a run's facts with their query lineage attached."""
    if not facts:
        return []
    out = []
    async with pool().acquire() as conn:
        async with conn.transaction():
            for f in facts:
                row = await conn.fetchrow(
                    """INSERT INTO canvas.facts
                         (canvas_id, kind, entity, label, unit, as_of, value, points,
                          tool, query, confidence, query_id, run_id)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'warehouse_query',$9,$10,$11,$12)
                       RETURNING id, kind, label, unit, value, points, confidence""",
                    uid(canvas_id),
                    f.get("kind") or "scalar",
                    f.get("entity"),
                    f.get("label"),
                    f.get("unit"),
                    f.get("asOf"),
                    f.get("value"),
                    f.get("points"),
                    f.get("queryLabel"),
                    f.get("confidence") or "illustrative",
                    uid(query_id),
                    uid(run_id),
                )
                out.append({"factId": str(row["id"]), **{k: row[k] for k in row.keys() if k != "id"}})
    return out


# ---- read-time materialisation ------------------------------------------------------------


def materialize(kind: str, spec: dict[str, Any], bindings: Sequence[dict[str, Any]] | None,
                facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Write a fact set into a spec template at its bound paths.

    The same operation `commands.bind_and_materialize` performs at write time, but
    against a fact map chosen by the caller rather than looked up from the canvas —
    which is what lets one stored widget render differently for two viewers without
    either of them seeing the other's numbers.

    Unresolvable bindings are left as they are rather than raising: a viewer entitled
    to less should see a partial tile with a disclosure, not an error page.
    """
    spec = json.loads(json.dumps(spec))  # never mutate the caller's cached copy
    axis_set = False
    for b in bindings or []:
        fact = facts.get(str(b.get("factId") or ""))
        path = b.get("path")
        if not fact or not path:
            continue
        parts = _norm_path(path)
        if fact.get("kind") == "series":
            points = fact.get("points") or []
            ys = [p.get("y") for p in points]
            target = _get_at(spec, parts)
            if isinstance(target, dict) and "data" in target:
                target["data"] = ys
                # Align the axis to THIS viewer's points, from the first series binding.
                # Keeping the template's categories would name the very rows a narrowed
                # viewer is not entitled to see — the withheld count says "4 regions"
                # while the axis labels spell them out. The axis must match the data.
                if not axis_set:
                    spec.setdefault("xAxis", {})
                    spec["xAxis"]["categories"] = [p.get("x") for p in points]
                    axis_set = True
            else:
                _set_at(spec, parts, ys)
        else:
            _set_at(spec, parts, fact.get("value"))
    return spec


async def latest_facts_for_queries(
    query_ids: Sequence[Any], principals: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """The most recent facts each query produced *for these principals*.

    Partitioned by `ran_as` so a refresh Ada ran can never satisfy a resolve for Blake.
    A viewer with no run of their own gets nothing back and the tile re-executes.
    """
    if not query_ids:
        return {}
    rows = await pool().fetch(
        """SELECT DISTINCT ON (f.query_id)
                  f.id, f.query_id, f.kind, f.label, f.unit, f.value, f.points,
                  f.confidence, f.as_of, r.withheld, r.status
           FROM canvas.facts f
           JOIN canvas.query_runs r ON r.id = f.run_id
           WHERE f.query_id = ANY($1::uuid[]) AND r.ran_as = ANY($2::text[])
           ORDER BY f.query_id, f.created_at DESC""",
        [uid(q) for q in query_ids],
        list(principals),
    )
    return {str(r["id"]): dict(r) for r in rows}

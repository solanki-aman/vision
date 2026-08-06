"""PostgreSQL access. Schemas mirror the plan: canvas.*, conversation.*, audit.*."""

import json
import logging
import uuid
from typing import Any, Iterable, Sequence

import asyncpg

from .config import settings

log = logging.getLogger("vision.db")

_pool: asyncpg.Pool | None = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not initialised")
    return _pool


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Hand JSONB back as Python objects instead of strings."""
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


def uid(value: Any) -> uuid.UUID:
    """asyncpg is strict about UUID parameters; callers hand us strings."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def rows_to_dicts(rows: Iterable[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS canvas;
CREATE SCHEMA IF NOT EXISTS conversation;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS canvas.canvases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL DEFAULT 'Untitled canvas',
  current_version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS canvas.widgets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canvas_id UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  spec JSONB NOT NULL,
  provenance JSONB,
  current_version INT NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_widgets_canvas ON canvas.widgets (canvas_id, status);

CREATE TABLE IF NOT EXISTS canvas.placements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canvas_id UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  widget_id UUID NOT NULL UNIQUE REFERENCES canvas.widgets(id) ON DELETE CASCADE,
  x INT NOT NULL, y INT NOT NULL, w INT NOT NULL, h INT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_placements_canvas ON canvas.placements (canvas_id, y, x);

CREATE TABLE IF NOT EXISTS canvas.facts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canvas_id UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'scalar',
  entity TEXT,
  label TEXT NOT NULL,
  unit TEXT,
  as_of TEXT,
  value DOUBLE PRECISION,
  points JSONB,
  tool TEXT NOT NULL DEFAULT 'web_search',
  query TEXT,
  snippet TEXT,
  source_url TEXT,
  confidence TEXT NOT NULL DEFAULT 'measured',
  derived_from JSONB,
  formula TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_facts_canvas ON canvas.facts (canvas_id, created_at);

CREATE TABLE IF NOT EXISTS canvas.change_sets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canvas_id UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  origin TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'local-user',
  status TEXT NOT NULL,
  operations JSONB NOT NULL,
  inverse JSONB,
  undone BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_change_sets_canvas ON canvas.change_sets (canvas_id, created_at DESC);

CREATE TABLE IF NOT EXISTS canvas.versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type TEXT NOT NULL,
  entity_id UUID NOT NULL,
  version_number INT NOT NULL,
  definition JSONB NOT NULL,
  change_set_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (entity_type, entity_id, version_number)
);

CREATE TABLE IF NOT EXISTS audit.events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id UUID,
  outcome TEXT NOT NULL,
  metadata JSONB,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit.events (occurred_at DESC);

CREATE TABLE IF NOT EXISTS conversation.messages (
  id TEXT NOT NULL,
  canvas_id UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  parts JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (canvas_id, id)
);
CREATE INDEX IF NOT EXISTS idx_messages_canvas ON conversation.messages (canvas_id, created_at);
"""

# Early builds keyed messages on id alone, so assistant ids collided across canvases.
MESSAGE_PK_REPAIR = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema='conversation' AND table_name='messages'
      AND constraint_type='PRIMARY KEY' AND constraint_name='messages_pkey'
  ) AND (
    SELECT count(*) FROM information_schema.key_column_usage
    WHERE table_schema='conversation' AND table_name='messages'
      AND constraint_name='messages_pkey'
  ) = 1 THEN
    DELETE FROM conversation.messages WHERE id = '';
    ALTER TABLE conversation.messages DROP CONSTRAINT messages_pkey;
    ALTER TABLE conversation.messages ADD PRIMARY KEY (canvas_id, id);
  END IF;
END $$;
"""


async def init_db() -> None:
    global _pool
    # asyncpg wants postgresql://; the compose file and the TypeScript prototype use postgres://.
    dsn = settings.database_url.replace("postgres://", "postgresql://", 1)
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_connection)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)
        await conn.execute("ALTER TABLE canvas.canvases ADD COLUMN IF NOT EXISTS style JSONB")
        await conn.execute("ALTER TABLE canvas.widgets ADD COLUMN IF NOT EXISTS bindings JSONB")
        await conn.execute("ALTER TABLE canvas.facts ADD COLUMN IF NOT EXISTS inputs JSONB")
        # created_at is transaction-start time in Postgres, so every row a single
        # save_messages() call writes shares one timestamp — a user prompt and its
        # own reply can tie. seq is a true insertion-order tiebreak.
        await conn.execute("ALTER TABLE conversation.messages ADD COLUMN IF NOT EXISTS seq BIGSERIAL")
        await conn.execute(MESSAGE_PK_REPAIR)


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def audit(
    action: str,
    outcome: str,
    target_type: str | None = None,
    target_id: Any | None = None,
    metadata: Any | None = None,
) -> None:
    try:
        await pool().execute(
            """INSERT INTO audit.events (actor, action, target_type, target_id, outcome, metadata)
               VALUES ('local-user', $1, $2, $3, $4, $5)""",
            action,
            target_type,
            uid(target_id) if target_id else None,
            outcome,
            metadata,
        )
    except Exception:  # audit must never break the request path
        log.exception("audit write failed")


async def list_canvases() -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT c.id, c.title, c.updated_at,
                  (SELECT count(*) FROM canvas.widgets w
                   WHERE w.canvas_id = c.id AND w.status = 'active') AS widget_count
           FROM canvas.canvases c ORDER BY c.updated_at DESC LIMIT 100"""
    )
    # The web client renders widget_count as text, matching the pg driver's bigint-as-string.
    return [{**dict(r), "widget_count": str(r["widget_count"])} for r in rows]


async def create_canvas(title: str | None = None) -> dict[str, Any]:
    row = await pool().fetchrow(
        "INSERT INTO canvas.canvases (title) VALUES ($1) RETURNING id, title, updated_at",
        title or "Untitled canvas",
    )
    await audit("create_canvas", "applied", "canvas", row["id"])
    return dict(row)


async def rename_canvas_if_untitled(canvas_id: Any, title: str) -> None:
    await pool().execute(
        """UPDATE canvas.canvases SET title = $2, updated_at = now()
           WHERE id = $1 AND title = 'Untitled canvas'""",
        uid(canvas_id),
        title[:70],
    )


async def get_canvas_state(canvas_id: Any) -> dict[str, Any]:
    cid = uid(canvas_id)
    widgets = await pool().fetch(
        """SELECT w.id, w.kind, w.title, w.spec, w.provenance, w.bindings, w.current_version,
                  p.x, p.y, p.w, p.h
           FROM canvas.widgets w
           LEFT JOIN canvas.placements p ON p.widget_id = w.id
           WHERE w.canvas_id = $1 AND w.status = 'active'
           ORDER BY p.y NULLS LAST, p.x NULLS LAST""",
        cid,
    )
    meta = await pool().fetchrow(
        "SELECT id, title, current_version, style FROM canvas.canvases WHERE id = $1", cid
    )
    return {"canvas": dict(meta) if meta else None, "widgets": rows_to_dicts(widgets)}


async def get_canvas_summary(canvas_id: Any) -> str:
    """Compact, token-bounded canvas summary handed to the agent each turn (plan §3.2)."""
    state = await get_canvas_state(canvas_id)
    canvas, widgets = state["canvas"], state["widgets"]
    style = (canvas or {}).get("style")
    if style:
        style_line = (
            f'Canvas identity: "{style.get("name")}" (accent {style.get("accent")}, '
            f'{style.get("type")}, {style.get("paper")} paper, {style.get("cards")} cards).'
        )
    else:
        style_line = "Canvas identity: NOT SET — call set_style before building."

    if not widgets:
        return f"The canvas is empty. {style_line}"

    lines = []
    for w in widgets[:24]:
        spec = w.get("spec") or {}
        kind = w["kind"]
        detail = ""
        if kind == "chart":
            names = ", ".join(s.get("name", "") for s in spec.get("series", []))
            categories = (spec.get("xAxis") or {}).get("categories") or []
            axis = (
                f' on {len(categories)} categories [{categories[0]}..{categories[-1]}]'
                if categories
                else ""
            )
            detail = f'{spec.get("chartType")}{axis}, series: {names}'
        elif kind == "kpi":
            detail = f'{spec.get("label")}: {spec.get("value")}{spec.get("unit") or ""}'
        elif kind == "table":
            detail = f'{len(spec.get("rows", []))} rows'
        elif kind == "narrative":
            detail = str(spec.get("body", ""))[:80]
        elif kind == "image":
            detail = str(spec.get("prompt", ""))[:60]
        at = "unplaced" if w["x"] is None else f'x{w["x"]} y{w["y"]} {w["w"]}x{w["h"]}'
        lines.append(f'- {w["id"]} | {kind} | "{w["title"]}" | {at} | {detail}')

    body = "\n".join(lines)

    fact_rows = await pool().fetch(
        """SELECT id, kind, entity, label, unit, as_of, value FROM canvas.facts
           WHERE canvas_id = $1 ORDER BY created_at DESC LIMIT 40""",
        uid(canvas_id),
    )
    if fact_rows:
        flines = []
        for f in fact_rows:
            if f["value"] is not None:
                val = f'{f["value"]}{f["unit"] or ""}'
            else:
                val = "series" if f["kind"] == "series" else "?"
            asof = f' as of {f["as_of"]}' if f["as_of"] else ""
            flines.append(f'- {f["id"]} | {f["entity"] or ""} · {f["label"]} = {val}{asof}')
        facts_block = (
            "\n\nAvailable facts — bind every measured number to one of these ids "
            "instead of typing it:\n" + "\n".join(flines)
        )
    else:
        facts_block = ""

    return (
        f"{style_line}\nCanvas has {len(widgets)} widget(s), "
        f"grid is 12 columns wide:\n{body}{facts_block}"
    )


async def set_canvas_style(canvas_id: Any, style: Any) -> None:
    await pool().execute(
        "UPDATE canvas.canvases SET style = $2, updated_at = now() WHERE id = $1",
        uid(canvas_id),
        style,
    )
    await audit("set_style", "applied", "canvas", canvas_id, style)


async def record_facts(
    canvas_id: Any,
    facts: Sequence[dict[str, Any]],
    *,
    tool: str,
    query: str | None,
) -> list[dict[str, Any]]:
    """Persist retrieved facts with their lineage; returns them with assigned ids.

    This is the only place a number enters the canvas with provenance: search
    extracts it, this stores value + source + query, later a widget binds to the id.
    """
    cid = uid(canvas_id)
    out: list[dict[str, Any]] = []
    async with pool().acquire() as conn:
        async with conn.transaction():
            for f in facts:
                row = await conn.fetchrow(
                    """INSERT INTO canvas.facts
                         (canvas_id, kind, entity, label, unit, as_of, value, points,
                          tool, query, snippet, source_url, confidence, derived_from, formula, inputs)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                       RETURNING id, kind, entity, label, unit, as_of, value, points,
                                 source_url, confidence""",
                    cid,
                    f.get("kind") or "scalar",
                    f.get("entity"),
                    f.get("label"),
                    f.get("unit"),
                    f.get("asOf"),
                    f.get("value"),
                    f.get("points"),
                    tool,
                    query,
                    f.get("snippet"),
                    f.get("sourceUrl"),
                    f.get("confidence") or "measured",
                    f.get("derivedFrom"),
                    f.get("formula"),
                    f.get("inputs"),
                )
                out.append({"factId": str(row["id"]), **{k: row[k] for k in row.keys() if k != "id"}})
    await audit("record_facts", "applied", "canvas", canvas_id, {"count": len(out), "tool": tool})
    return out


async def get_facts(canvas_id: Any) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT id, kind, entity, label, unit, as_of, value, points, tool, query,
                  snippet, source_url, confidence, derived_from, formula, inputs, created_at
           FROM canvas.facts WHERE canvas_id = $1 ORDER BY created_at""",
        uid(canvas_id),
    )
    return rows_to_dicts(rows)


async def get_facts_by_ids(canvas_id: Any, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The subset of a canvas's facts named by id — inputs for a compute step."""
    if not ids:
        return {}
    try:
        uids = [uid(i) for i in ids]
    except (ValueError, AttributeError, TypeError):
        return {}
    rows = await pool().fetch(
        """SELECT id, kind, entity, label, unit, value, points, confidence
           FROM canvas.facts WHERE canvas_id = $1 AND id = ANY($2::uuid[])""",
        uid(canvas_id),
        uids,
    )
    return {str(r["id"]): dict(r) for r in rows}


async def get_messages(canvas_id: Any) -> list[dict[str, Any]]:
    # seq (insertion order), not created_at: a turn's user prompt and its own
    # reply are written in one transaction and can share a timestamp.
    rows = await pool().fetch(
        """SELECT id, role, parts FROM conversation.messages
           WHERE canvas_id = $1 ORDER BY seq""",
        uid(canvas_id),
    )
    return rows_to_dicts(rows)


async def save_messages(canvas_id: Any, messages: Sequence[dict[str, Any]]) -> None:
    cid = uid(canvas_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            for i, m in enumerate(messages):
                mid = (m.get("id") or "").strip() or f'{m.get("role")}-{i}'
                await conn.execute(
                    """INSERT INTO conversation.messages (id, canvas_id, role, parts)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (canvas_id, id) DO UPDATE SET parts = EXCLUDED.parts""",
                    mid,
                    cid,
                    m.get("role"),
                    m.get("parts"),
                )
            await conn.execute(
                "UPDATE canvas.canvases SET updated_at = now() WHERE id = $1", cid
            )


async def get_history(canvas_id: Any) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT id, origin, status, operations, undone, created_at
           FROM canvas.change_sets WHERE canvas_id = $1
           ORDER BY created_at DESC LIMIT 40""",
        uid(canvas_id),
    )
    return rows_to_dicts(rows)

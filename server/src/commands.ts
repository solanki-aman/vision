import type pg from "pg";
import { pool, audit } from "./db.js";
import { validateSpec, type WidgetKind } from "./specs.js";

export type Operation =
  | { kind: "add_widget"; widgetKind: WidgetKind; title: string; spec: unknown; provenance?: unknown; size?: { w: number; h: number } }
  | { kind: "update_widget"; widgetId: string; title?: string; spec?: unknown; provenance?: unknown }
  | { kind: "remove_widget"; widgetId: string }
  | { kind: "move_widget"; widgetId: string; x: number; y: number }
  | { kind: "resize_widget"; widgetId: string; w: number; h: number };

export interface ApplyResult {
  changeSetId: string;
  applied: { operation: Operation; widgetId: string }[];
  errors: string[];
}

const GRID_COLS = 12;

/** Row heights in grid units. One unit is 40px, so a kpi band is 120px. */
export const HEIGHTS = { label: 1, kpi: 4, short: 6, standard: 8, tall: 11 } as const;
export type HeightName = keyof typeof HEIGHTS;

const DEFAULT_SIZE: Record<WidgetKind, { w: number; h: number }> = {
  chart: { w: 6, h: HEIGHTS.standard },
  kpi: { w: 3, h: HEIGHTS.kpi },
  table: { w: 6, h: HEIGHTS.standard },
  narrative: { w: 4, h: HEIGHTS.standard },
  image: { w: 4, h: HEIGHTS.standard },
  control: { w: 12, h: 2 },
  label: { w: 12, h: 1 },
  statement: { w: 5, h: HEIGHTS.tall },
};

// Forms that need width or breathing room when the agent does not say.
const CHART_SIZE: Record<string, { w: number; h: number }> = {
  gauge: { w: 3, h: HEIGHTS.short },
  pie: { w: 4, h: HEIGHTS.standard },
  donut: { w: 4, h: HEIGHTS.standard },
  rose: { w: 4, h: HEIGHTS.standard },
  funnel: { w: 4, h: HEIGHTS.standard },
  radar: { w: 4, h: HEIGHTS.standard },
  sunburst: { w: 5, h: HEIGHTS.tall },
  treemap: { w: 6, h: HEIGHTS.standard },
  tree: { w: 6, h: HEIGHTS.standard },
  sankey: { w: 8, h: HEIGHTS.tall },
  chord: { w: 5, h: HEIGHTS.tall },
  graph: { w: 5, h: HEIGHTS.tall },
  calendar: { w: 12, h: HEIGHTS.short },
  heatmap: { w: 7, h: HEIGHTS.standard },
  parallel: { w: 8, h: HEIGHTS.standard },
  theme_river: { w: 8, h: HEIGHTS.standard },
  candlestick: { w: 8, h: HEIGHTS.standard },
  boxplot: { w: 6, h: HEIGHTS.standard },
};

function sizeFor(kind: WidgetKind, spec: unknown) {
  if (kind === "chart") {
    const type = (spec as { chartType?: string })?.chartType;
    if (type && CHART_SIZE[type]) return CHART_SIZE[type];
  }
  return DEFAULT_SIZE[kind];
}

export interface LayoutRow {
  height: HeightName;
  items: { widgetId: string; span: number }[];
}

export interface LayoutLane {
  span: number;
  items: { widgetId: string; height: HeightName }[];
}

/** Lanes are vertical stacks side by side — the shape a flow or pipeline needs. */
export async function applyLanes(canvasId: string, lanes: LayoutLane[]) {
  const { rows: existing } = await pool.query(
    `SELECT widget_id FROM canvas.placements WHERE canvas_id = $1`,
    [canvasId],
  );
  const known = new Set(existing.map((r) => r.widget_id));
  const seen = new Set<string>();
  const errors: string[] = [];

  const total = lanes.reduce((a, l) => a + Math.max(1, l.span), 0) || 1;
  let x = 0;
  let maxY = 0;

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    for (const [idx, lane] of lanes.entries()) {
      let w = Math.max(2, Math.round((Math.max(1, lane.span) / total) * GRID_COLS));
      if (idx === lanes.length - 1) w = Math.max(2, GRID_COLS - x);
      w = Math.min(w, GRID_COLS - x);
      if (w <= 0) continue;

      let y = 0;
      for (const item of lane.items) {
        if (!known.has(item.widgetId)) {
          errors.push(`unknown widget ${item.widgetId}`);
          continue;
        }
        if (seen.has(item.widgetId)) continue;
        const h = HEIGHTS[item.height] ?? HEIGHTS.standard;
        seen.add(item.widgetId);
        await client.query(
          `UPDATE canvas.placements SET x = $2, y = $3, w = $4, h = $5 WHERE widget_id = $1`,
          [item.widgetId, x, y, w, h],
        );
        y += h;
      }
      maxY = Math.max(maxY, y);
      x += w;
    }

    for (const id of known) {
      if (seen.has(id)) continue;
      await client.query(`UPDATE canvas.placements SET x = 0, y = $2, w = 12 WHERE widget_id = $1`, [id, maxY]);
      maxY += HEIGHTS.standard;
    }
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }

  await audit("apply_lanes", errors.length ? "partial" : "applied", "canvas", canvasId, {
    lanes: lanes.length,
    errors,
  });
  return { lanes: lanes.length, placed: seen.size, errors };
}

/**
 * Row-based layout: every card in a row shares one height and the spans are
 * normalised to fill 12 columns. This is what makes a canvas read as a
 * dashboard rather than a pile of boxes.
 */
export async function applyLayout(canvasId: string, rows: LayoutRow[]) {
  const { rows: existing } = await pool.query(
    `SELECT widget_id FROM canvas.placements WHERE canvas_id = $1`,
    [canvasId],
  );
  const known = new Set(existing.map((r) => r.widget_id));
  const seen = new Set<string>();
  const errors: string[] = [];
  let y = 0;

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    for (const row of rows) {
      const items = row.items.filter((i) => {
        if (!known.has(i.widgetId)) {
          errors.push(`unknown widget ${i.widgetId}`);
          return false;
        }
        if (seen.has(i.widgetId)) return false;
        return true;
      });
      if (!items.length) continue;

      const h = HEIGHTS[row.height] ?? HEIGHTS.standard;
      const total = items.reduce((a, i) => a + Math.max(1, i.span), 0);
      let x = 0;
      for (const [idx, item] of items.entries()) {
        // Scale the row to exactly 12 columns, giving any remainder to the last card.
        const raw = Math.max(1, item.span);
        let w = Math.max(2, Math.round((raw / total) * GRID_COLS));
        if (idx === items.length - 1) w = Math.max(2, GRID_COLS - x);
        w = Math.min(w, GRID_COLS - x);
        if (w <= 0) continue;
        seen.add(item.widgetId);
        await client.query(
          `UPDATE canvas.placements SET x = $2, y = $3, w = $4, h = $5 WHERE widget_id = $1`,
          [item.widgetId, x, y, w, h],
        );
        x += w;
      }
      y += h;
    }

    // Anything the agent left out keeps its size and stacks below.
    for (const id of known) {
      if (seen.has(id)) continue;
      await client.query(`UPDATE canvas.placements SET x = 0, y = $2 WHERE widget_id = $1`, [id, y]);
      y += HEIGHTS.standard;
    }
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }

  await audit("apply_layout", errors.length ? "partial" : "applied", "canvas", canvasId, {
    rows: rows.length,
    errors,
  });
  return { rows: rows.length, placed: seen.size, errors };
}

/** Next free row-major slot in a 12-col grid — placements are server-generated (plan §2.10). */
async function nextSlot(client: pg.PoolClient, canvasId: string, w: number, h: number) {
  const { rows } = await client.query(
    `SELECT x, y, w, h FROM canvas.placements WHERE canvas_id = $1`,
    [canvasId],
  );
  const taken = (x: number, y: number) =>
    rows.some((p) => x < p.x + p.w && x + w > p.x && y < p.y + p.h && y + h > p.y);
  for (let y = 0; y < 200; y++) {
    for (let x = 0; x + w <= GRID_COLS; x++) {
      if (!taken(x, y)) return { x, y };
    }
  }
  return { x: 0, y: 200 };
}

async function snapshot(client: pg.PoolClient, widgetId: string, changeSetId: string) {
  const { rows } = await client.query(
    `SELECT kind, title, spec, provenance, current_version FROM canvas.widgets WHERE id = $1`,
    [widgetId],
  );
  if (!rows[0]) return null;
  const w = rows[0];
  await client.query(
    `INSERT INTO canvas.versions (entity_type, entity_id, version_number, definition, change_set_id)
     VALUES ('widget', $1, $2, $3, $4)
     ON CONFLICT (entity_type, entity_id, version_number) DO NOTHING`,
    [widgetId, w.current_version, JSON.stringify(w), changeSetId],
  );
  return w;
}

/** Validate then apply a typed change set in one transaction. The agent never writes storage directly. */
export async function applyChangeSet(
  canvasId: string,
  operations: Operation[],
  origin: string,
): Promise<ApplyResult> {
  const errors: string[] = [];
  const applied: { operation: Operation; widgetId: string }[] = [];
  const inverse: Operation[] = [];

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const { rows: csRows } = await client.query(
      `INSERT INTO canvas.change_sets (canvas_id, origin, status, operations)
       VALUES ($1, $2, 'validating', $3) RETURNING id`,
      [canvasId, origin, JSON.stringify(operations)],
    );
    const changeSetId: string = csRows[0].id;

    for (const op of operations) {
      switch (op.kind) {
        case "add_widget": {
          const parsed = validateSpec(op.widgetKind, op.spec);
          if (!parsed.success) {
            errors.push(`add_widget "${op.title}": ${parsed.error.issues.map((i) => `${i.path.join(".")} ${i.message}`).join("; ")}`);
            continue;
          }
          const size = op.size ?? sizeFor(op.widgetKind, parsed.data);
          const { x, y } = await nextSlot(client, canvasId, size.w, size.h);
          const { rows } = await client.query(
            `INSERT INTO canvas.widgets (canvas_id, kind, title, spec, provenance)
             VALUES ($1, $2, $3, $4, $5) RETURNING id`,
            [canvasId, op.widgetKind, op.title, JSON.stringify(parsed.data), op.provenance ? JSON.stringify(op.provenance) : null],
          );
          const widgetId = rows[0].id;
          await client.query(
            `INSERT INTO canvas.placements (canvas_id, widget_id, x, y, w, h)
             VALUES ($1, $2, $3, $4, $5, $6)`,
            [canvasId, widgetId, x, y, size.w, size.h],
          );
          await client.query(
            `INSERT INTO canvas.versions (entity_type, entity_id, version_number, definition, change_set_id)
             VALUES ('widget', $1, 1, $2, $3)`,
            [widgetId, JSON.stringify({ kind: op.widgetKind, title: op.title, spec: parsed.data }), changeSetId],
          );
          applied.push({ operation: op, widgetId });
          inverse.push({ kind: "remove_widget", widgetId });
          break;
        }

        case "update_widget": {
          const prev = await snapshot(client, op.widgetId, changeSetId);
          if (!prev) {
            errors.push(`update_widget: ${op.widgetId} not found`);
            continue;
          }
          let spec = prev.spec;
          if (op.spec !== undefined) {
            const parsed = validateSpec(prev.kind as WidgetKind, op.spec);
            if (!parsed.success) {
              errors.push(`update_widget ${op.widgetId}: invalid spec`);
              continue;
            }
            spec = parsed.data;
          }
          await client.query(
            `UPDATE canvas.widgets
             SET title = $2, spec = $3, provenance = COALESCE($4, provenance),
                 current_version = current_version + 1, updated_at = now()
             WHERE id = $1`,
            [op.widgetId, op.title ?? prev.title, JSON.stringify(spec), op.provenance ? JSON.stringify(op.provenance) : null],
          );
          applied.push({ operation: op, widgetId: op.widgetId });
          inverse.push({ kind: "update_widget", widgetId: op.widgetId, title: prev.title, spec: prev.spec });
          break;
        }

        case "remove_widget": {
          const prev = await snapshot(client, op.widgetId, changeSetId);
          if (!prev) {
            errors.push(`remove_widget: ${op.widgetId} not found`);
            continue;
          }
          await client.query(`UPDATE canvas.widgets SET status = 'trashed' WHERE id = $1`, [op.widgetId]);
          await client.query(`DELETE FROM canvas.placements WHERE widget_id = $1`, [op.widgetId]);
          applied.push({ operation: op, widgetId: op.widgetId });
          inverse.push({
            kind: "add_widget",
            widgetKind: prev.kind as WidgetKind,
            title: prev.title,
            spec: prev.spec,
            provenance: prev.provenance,
          });
          break;
        }

        case "move_widget":
        case "resize_widget": {
          const { rows } = await client.query(
            `SELECT x, y, w, h FROM canvas.placements WHERE widget_id = $1`,
            [op.widgetId],
          );
          if (!rows[0]) {
            errors.push(`${op.kind}: placement for ${op.widgetId} not found`);
            continue;
          }
          const p = rows[0];
          if (op.kind === "move_widget") {
            const x = Math.max(0, Math.min(GRID_COLS - p.w, op.x));
            await client.query(`UPDATE canvas.placements SET x = $2, y = $3 WHERE widget_id = $1`, [op.widgetId, x, Math.max(0, op.y)]);
            inverse.push({ kind: "move_widget", widgetId: op.widgetId, x: p.x, y: p.y });
          } else {
            const w = Math.max(2, Math.min(GRID_COLS, op.w));
            await client.query(`UPDATE canvas.placements SET w = $2, h = $3 WHERE widget_id = $1`, [op.widgetId, w, Math.max(2, op.h)]);
            inverse.push({ kind: "resize_widget", widgetId: op.widgetId, w: p.w, h: p.h });
          }
          applied.push({ operation: op, widgetId: op.widgetId });
          break;
        }
      }
    }

    await client.query(
      `UPDATE canvas.change_sets SET status = $2, inverse = $3 WHERE id = $1`,
      [changeSetId, errors.length && !applied.length ? "rejected" : "applied", JSON.stringify(inverse.reverse())],
    );
    await client.query(
      `UPDATE canvas.canvases SET current_version = current_version + 1, updated_at = now() WHERE id = $1`,
      [canvasId],
    );
    await client.query("COMMIT");

    await audit("apply_change_set", errors.length ? "partial" : "applied", "canvas", canvasId, {
      changeSetId,
      opCount: operations.length,
      appliedCount: applied.length,
      errors,
    });
    return { changeSetId, applied, errors };
  } catch (e) {
    await client.query("ROLLBACK");
    await audit("apply_change_set", "failed", "canvas", canvasId, { error: String(e) });
    throw e;
  } finally {
    client.release();
  }
}

/**
 * The agent can request overlapping placements. Sweep row-major and push each
 * widget down to the first free slot at or below its requested row.
 */
export async function compact(canvasId: string) {
  const { rows } = await pool.query(
    `SELECT widget_id, x, y, w, h FROM canvas.placements
     WHERE canvas_id = $1 ORDER BY y, x`,
    [canvasId],
  );
  const placed: { x: number; y: number; w: number; h: number }[] = [];
  const updates: { id: string; y: number }[] = [];

  for (const p of rows) {
    const w = Math.min(GRID_COLS, Math.max(1, p.w));
    const x = Math.max(0, Math.min(GRID_COLS - w, p.x));
    let y = Math.max(0, p.y);
    const hits = (ty: number) =>
      placed.some((o) => x < o.x + o.w && x + w > o.x && ty < o.y + o.h && ty + p.h > o.y);
    while (hits(y)) y += 1;
    placed.push({ x, y, w, h: p.h });
    if (y !== p.y || x !== p.x) updates.push({ id: p.widget_id, y });
  }

  for (const u of updates) {
    await pool.query(`UPDATE canvas.placements SET y = $2 WHERE widget_id = $1`, [u.id, u.y]);
  }
  return updates.length;
}

/** Undo is a new change set of stored inverse operations — history is never deleted (plan §2.8). */
export async function undoLast(canvasId: string) {
  const { rows } = await pool.query(
    `SELECT id, inverse FROM canvas.change_sets
     WHERE canvas_id = $1 AND status = 'applied' AND undone = false AND inverse IS NOT NULL
     ORDER BY created_at DESC LIMIT 1`,
    [canvasId],
  );
  if (!rows[0]) return null;
  const result = await applyChangeSet(canvasId, rows[0].inverse as Operation[], "undo");
  await pool.query(`UPDATE canvas.change_sets SET undone = true WHERE id = $1`, [rows[0].id]);
  return result;
}

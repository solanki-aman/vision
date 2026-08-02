import pg from "pg";

export const pool = new pg.Pool({
  connectionString:
    process.env.DATABASE_URL ?? "postgres://vision:vision@localhost:5433/vision",
});

export async function initDb() {
  await pool.query(`
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
  `);

  // Early builds keyed messages on id alone, so assistant ids collided across canvases.
  await pool.query(`
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
  `);
}

export async function audit(
  action: string,
  outcome: string,
  targetType?: string,
  targetId?: string | null,
  metadata?: unknown,
) {
  await pool
    .query(
      `INSERT INTO audit.events (actor, action, target_type, target_id, outcome, metadata)
       VALUES ('local-user', $1, $2, $3, $4, $5)`,
      [action, targetType ?? null, targetId ?? null, outcome, metadata ? JSON.stringify(metadata) : null],
    )
    .catch((e) => console.error("audit write failed", e));
}

export async function listCanvases() {
  const { rows } = await pool.query(
    `SELECT c.id, c.title, c.updated_at,
            (SELECT count(*) FROM canvas.widgets w
             WHERE w.canvas_id = c.id AND w.status = 'active') AS widget_count
     FROM canvas.canvases c ORDER BY c.updated_at DESC LIMIT 100`,
  );
  return rows;
}

export async function createCanvas(title?: string) {
  const { rows } = await pool.query(
    `INSERT INTO canvas.canvases (title) VALUES ($1) RETURNING id, title, updated_at`,
    [title ?? "Untitled canvas"],
  );
  await audit("create_canvas", "applied", "canvas", rows[0].id);
  return rows[0];
}

export async function renameCanvasIfUntitled(canvasId: string, title: string) {
  await pool.query(
    `UPDATE canvas.canvases SET title = $2, updated_at = now()
     WHERE id = $1 AND title = 'Untitled canvas'`,
    [canvasId, title.slice(0, 70)],
  );
}

export async function getCanvasState(canvasId: string) {
  const { rows: widgets } = await pool.query(
    `SELECT w.id, w.kind, w.title, w.spec, w.provenance, w.current_version,
            p.x, p.y, p.w, p.h
     FROM canvas.widgets w
     LEFT JOIN canvas.placements p ON p.widget_id = w.id
     WHERE w.canvas_id = $1 AND w.status = 'active'
     ORDER BY p.y NULLS LAST, p.x NULLS LAST`,
    [canvasId],
  );
  const { rows: meta } = await pool.query(
    `SELECT id, title, current_version FROM canvas.canvases WHERE id = $1`,
    [canvasId],
  );
  return { canvas: meta[0] ?? null, widgets };
}

/** Compact, token-bounded canvas summary handed to the agent each turn (plan §3.2). */
export async function getCanvasSummary(canvasId: string) {
  const { widgets } = await getCanvasState(canvasId);
  if (widgets.length === 0) return "The canvas is empty.";
  const lines = widgets.slice(0, 24).map((w: any) => {
    const spec = w.spec ?? {};
    let detail = "";
    if (w.kind === "chart") detail = `${spec.chartType}, series: ${(spec.series ?? []).map((s: any) => s.name).join(", ")}`;
    else if (w.kind === "kpi") detail = `${spec.label}: ${spec.value}${spec.unit ?? ""}`;
    else if (w.kind === "table") detail = `${(spec.rows ?? []).length} rows`;
    else if (w.kind === "narrative") detail = String(spec.body ?? "").slice(0, 80);
    else if (w.kind === "image") detail = String(spec.prompt ?? "").slice(0, 60);
    return `- ${w.id} | ${w.kind} | "${w.title}" | ${detail}`;
  });
  return `Canvas has ${widgets.length} widget(s):\n${lines.join("\n")}`;
}

export async function getMessages(canvasId: string) {
  const { rows } = await pool.query(
    `SELECT id, role, parts FROM conversation.messages
     WHERE canvas_id = $1 ORDER BY created_at`,
    [canvasId],
  );
  return rows;
}

export async function saveMessages(
  canvasId: string,
  messages: { id: string; role: string; parts: unknown }[],
) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    for (const [i, m] of messages.entries()) {
      const id = m.id?.trim() || `${m.role}-${i}`;
      await client.query(
        `INSERT INTO conversation.messages (id, canvas_id, role, parts)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (canvas_id, id) DO UPDATE SET parts = EXCLUDED.parts`,
        [id, canvasId, m.role, JSON.stringify(m.parts)],
      );
    }
    await client.query(`UPDATE canvas.canvases SET updated_at = now() WHERE id = $1`, [canvasId]);
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}

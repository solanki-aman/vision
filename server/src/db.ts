import pg from "pg";

const pool = new pg.Pool({
  connectionString:
    process.env.DATABASE_URL ?? "postgres://vision:vision@localhost:5432/vision",
});

export async function initDb() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS conversations (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      title TEXT NOT NULL DEFAULT 'New conversation',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,
      conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      role TEXT NOT NULL,
      parts JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_messages_conversation
      ON messages (conversation_id, created_at);
  `);
}

export interface UIMessageRow {
  id: string;
  role: string;
  parts: unknown;
}

export async function listConversations() {
  const { rows } = await pool.query(
    `SELECT id, title, created_at, updated_at
     FROM conversations ORDER BY updated_at DESC LIMIT 100`,
  );
  return rows;
}

export async function createConversation(title?: string) {
  const { rows } = await pool.query(
    `INSERT INTO conversations (title) VALUES ($1) RETURNING id, title, created_at, updated_at`,
    [title ?? "New conversation"],
  );
  return rows[0];
}

export async function getMessages(conversationId: string): Promise<UIMessageRow[]> {
  const { rows } = await pool.query(
    `SELECT id, role, parts FROM messages
     WHERE conversation_id = $1 ORDER BY created_at`,
    [conversationId],
  );
  return rows;
}

export async function saveMessages(
  conversationId: string,
  messages: { id: string; role: string; parts: unknown }[],
) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    for (const m of messages) {
      await client.query(
        `INSERT INTO messages (id, conversation_id, role, parts)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (id) DO UPDATE SET parts = EXCLUDED.parts`,
        [m.id, conversationId, m.role, JSON.stringify(m.parts)],
      );
    }
    await client.query(
      `UPDATE conversations SET updated_at = now() WHERE id = $1`,
      [conversationId],
    );
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}

export async function setTitleIfNew(conversationId: string, title: string) {
  await pool.query(
    `UPDATE conversations SET title = $2
     WHERE id = $1 AND title = 'New conversation'`,
    [conversationId, title.slice(0, 80)],
  );
}

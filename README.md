# vision
Let agents express beyond language

## Vision prototype — charts-first agent

A chat app where the agent **always answers with visualizations**. Text is
only preamble/reasoning; every response carries one or more charts.

- **Model:** xAI Grok via the Vercel AI SDK (`@ai-sdk/xai`)
- **Frontend:** React + Vite + TypeScript, `useChat` from `@ai-sdk/react`
- **Charts:** Apache ECharts behind a typed spec → adapter (the model never
  emits renderer options or code)
- **Backend:** Express + AI SDK `streamText` with a `render_chart` tool
- **Persistence:** PostgreSQL (conversations + message parts as JSONB)
- **Local dev:** Docker Compose (postgres, server, web)

### Run

```bash
cp .env.example .env   # put your XAI_API_KEY in .env
docker compose up --build
```

Open http://localhost:5173. Postgres is exposed on host port **5433**
(5432 was already taken locally).

### Run without Docker (except postgres)

```bash
docker compose up -d postgres
cd server && DATABASE_URL=postgres://vision:vision@localhost:5433/vision XAI_API_KEY=... npm run dev
cd web && npm run dev
```

### How "always express in viz" works

1. `server/src/chat.ts` defines a strict Zod `chartSpecSchema` and a
   `render_chart` tool; the system prompt requires at least one tool call per
   response and caps prose at a short preamble.
2. Grok streams tool calls; the AI SDK surfaces them as typed message parts.
3. `web/src/chartAdapter.ts` deterministically translates the spec into an
   ECharts option using a validated, CVD-safe palette (`web/src/theme.ts`).
4. Finished messages (including tool parts) are persisted to Postgres and
   replayed identically when a conversation reopens.

# vision
Let agents express beyond language

An agent that answers on a **canvas**, not in a chat log. Ask a question and
charts, metrics, tables, annotations and generated images land on a live grid.
Words are demoted to a side rail: preamble, reasoning, and tool activity only.

- **Model:** xAI Grok (`grok-4.5`) via the Vercel AI SDK responses API
- **Live data:** xAI server-side agent tools — `web_search`, `x_search`, `code_execution`
- **Images:** Grok Imagine (`grok-imagine-image`)
- **Frontend:** React + Vite + TypeScript, GridStack layout, Apache ECharts
- **Backend:** Express + AI SDK `streamText`, typed command layer
- **Persistence:** PostgreSQL — `canvas.*`, `conversation.*`, `audit.*` schemas
- **Local dev:** Docker Compose (postgres, server, web)

## Run

```bash
cp .env.example .env   # put your XAI_API_KEY in .env
```

```bash
docker compose up --build
```

Open http://localhost:5173. Postgres is published on host port **5433**
(5432 is commonly taken by a local install).

To run the app processes directly against the containerised database:

```bash
docker compose up -d postgres
```

```bash
cd server && DATABASE_URL=postgres://vision:vision@localhost:5433/vision npm run dev
```

```bash
cd web && npm run dev
```

## How it works

**The agent proposes; a deterministic layer decides.** Grok never writes to the
database. It calls typed tools (`add_chart`, `add_kpi`, `add_table`,
`add_narrative`, `generate_image`, `update_widget`, `remove_widget`) whose
inputs are Zod schemas. Each call becomes a **change set** that
`server/src/commands.ts` validates, places on a 12-column grid, applies in one
transaction, snapshots as a version, and writes to the audit log. Invalid specs
are rejected with an error the model can read and retry against.

**Charts are data, not code.** The model sends a `VisualizationSpec` — chart
type, axes, series. `web/src/chartAdapter.ts` translates that into ECharts
options using a validated colorblind-safe palette. The model cannot pick
colors, emit renderer config, or ship executable code.

**Every widget carries provenance.** Source, as-of date, and a confidence of
`measured` / `estimated` / `illustrative`, rendered as a footer with a status
dot. Invented numbers are labelled as invented.

**Layout is bidirectional.** Dragging or resizing a widget emits
`move_widget` / `resize_widget` operations through the same command layer that
the agent uses, so direct manipulation and agent edits share one code path,
one version history, and one undo stack (`⌘Z`).

**Streaming.** Chat streams over the AI SDK UI message stream (reasoning parts
included). A separate SSE channel per canvas pushes change notifications, so
widgets appear the instant a command commits rather than when the turn ends.

## Relationship to the architecture plan

`canvas-technical-architecture.md` is the full enterprise design. This
prototype implements its load-bearing ideas — artifact/placement split, typed
change sets with inverse operations, append-only versions and audit, the
spec→renderer boundary, server-generated GridStack placements, SSE progress,
provenance disclosure, and the compact canvas summary handed to the agent each
turn. It deliberately omits the multi-tenant parts: entitlements, sharing,
scheduled refresh, exports, and the warehouse connector gateway.

## Keyboard

| Key | Action |
|---|---|
| `⌘K` | Focus the command bar |
| `⌘Z` | Undo the last change set |
| `Enter` | Send · `Shift+Enter` newline |

# vision

**Let agents express beyond language.**

Chat is a lossy medium. When you ask an LLM for the shape of your customer
base, or how mortgages actually work, or where money went last quarter, it
answers in paragraphs. The good answer was never words.

Vision is an agent that answers on a **canvas**. Ask a question and the model
composes a live layout of charts, metrics, tables, statements, generated
images, and short annotations. Prose gets demoted to a side rail —
preamble, reasoning, and tool activity only. The argument lives on the board.

The goal isn't a dashboard. It's an **argument, arranged in space**. A hero
that states the thesis, a spine chart that carries the flow, supporting
widgets that back it up, a narrative that closes it. Widgets reference each
other; the layout is the writing.

![$100 of AI infrastructure traced from investor dollar to inference token](screenshots/ai-dollar-flow.png)

<sub>Prompt: *"Trace $100 of AI infrastructure from investor dollar to inference token, as a sankey with lanes."* — four lanes read left to right: where the money **comes from**, where it **goes** (sankey spine + statement build), what you **get** (60x token-cost collapse, 65 GW power ceiling), and **so what** (a narrative that names the winner). Every widget is answering to the hero, not sitting beside it.</sub>

## Watch it build

<sub>Ninety-five seconds — a full CFO briefing built live, then edited twice. Watch the right rail: reasoning, live `web_search`, tool calls streaming as twelve widgets land on the board.</sub>

<video src="screenshots/demo.mp4" controls muted playsinline poster="screenshots/demo-poster.jpg" width="900"></video>

<sub>Video not rendering? [Download the MP4 directly.](screenshots/demo.mp4)</sub>

The video is one continuous session — three prompts, no cuts:

1. *"Give me a full CFO-level briefing on Nvidia right now. Real numbers via web search."* — Grok searches, then composes: a display-type hero, four KPIs, a stacked-bar of five quarters, a waterfall bridge of the H20-to-Blackwell margin recovery, a donut of segment mix, a hyperscaler-concentration bar chart, a full P&L statement, a five-quarter scorecard table, and a closing narrative. Every widget arrives with a source and confidence stamp.
2. *"Add AMD's data-center revenue to that revenue chart as a comparison series."* — one `add_chart_series` call. The chart stays put; a new green series appears alongside Nvidia's blue. Same widget ID, versioned in place. The point the chart is making — *the gap is the thesis* — becomes visible.
3. *"The tone is too clinical for what the numbers actually say. Give this the identity of a short thesis."* — one `set_style` call flips the canvas from an engineering-notebook feel to a warm cream-and-burnt-orange thesis. Every widget keeps its ID; the hero and copy re-tune to match the new voice.

No follow-up rebuilds the canvas. Every edit is a typed change set with a stored inverse — `⌘Z` steps back through them one at a time.

The final board:

![CFO briefing: The pick-and-shovel monopoly](screenshots/nvda-briefing.png)

---

## The idea

Grok is powerful because it can reason, search the web, and run code. But
those capabilities all funnel through a chat interface that flattens
everything into a scrolling wall of text. So this project asks:

- What if the model's output format was a **dashboard**, not a message?
- What if every generation was **direct-manipulable** — drag, resize, undo?
- What if the model **couldn't** emit rendering code, only structured facts?

The answer is a system where the model **proposes typed change sets** and a
deterministic command layer decides. Charts are data, not code. Colors come
from a validated palette, not from the model. Every widget carries provenance:
source, as-of date, and a confidence stamp. Invented numbers are labelled as
invented.

And critically: the agent is asked to compose an **argument**, not fill a
grid. The system prompt tells it to name its thesis before it picks the first
widget, to sequence widgets in the order a reader needs them, to write titles
that state findings rather than label topics, and to earn every widget's
place. When it works, the canvas reads like an opinion piece with data
embedded — not a page of tiles a reader has to assemble themselves.

## More examples

Every screenshot below was produced by asking one question, once. The agent
picked the style, chose the widgets, wrote the titles, and laid out the grid.
No templates, no post-editing.

Different question shapes want different canvas shapes. A performance
question wants a dashboard. A "how does it work" question wants lanes and
statements. A comparison question wants a spine chart and a settled-answer
footer. A story question wants a headline and a timeline. The system supports
all of them because layout is a decision the agent makes, not a template
picked from a catalog.

### A process, argued as a flow

![How a mortgage actually works](screenshots/mortgage-flow.png)

<sub>Prompt: *"Explain how a fixed-rate mortgage actually works."* — four lanes: setup → monthly mechanics → 30-year arc → lifetime outcome. Statements do the arithmetic, the area chart makes the front-loaded interest visible without a paragraph, and the last cell tells you interest costs more than the house itself.</sub>

### A finance dashboard from one question

![ARR up 40% while cash burns down](screenshots/arr-vs-cash.png)

<sub>Prompt: *"We're a Series B SaaS. ARR is up 40% but cash is falling fast. Show me the tension in one canvas."* — four KPIs, a divergence chart with legend, two waterfalls, a bullet-chart efficiency panel, and a donut of cash burn by category.</sub>

### A research comparison, argued

![Coffee caffeine by method](screenshots/coffee.png)

<sub>Prompt: *"Which brewing method actually delivers the most caffeine?"* — the agent decides the answer is "it depends on the axis," picks the highlight color to mark the winner in each dimension, and writes a settled-answer footer to close the argument.</sub>

### A market story with the beat that decided it

![Netflix vs Disney+ streaming wars](screenshots/streaming-wars-story.png)

<sub>Prompt: *"Tell me the story of the streaming wars, Netflix vs Disney+, 2019–2025."* — the hero states the outcome, the divergence chart shows exactly when Disney+ stopped growing, an annotation marks the Netflix Q2'22 recovery inflection, and a milestone table gives the event log. The layout **is** the argument: cause on the left, effect on the right.</sub>

### A workplace question with a diagnosis

![Attrition analysis](screenshots/attrition-story.png)

<sub>Prompt: *"Attrition jumped this year. Where's it coming from and what should we do?"* — headcount waterfall, department heatmap by tenure, exit-reason donut, and a narrative caveat about mistaking a manager problem for a pay problem.</sub>

### And when the question isn't about numbers at all

![Why we dream](screenshots/why-we-dream.png)

<sub>Prompt: *"Why do we dream?"* — the shape follows the question: a serif hero states the thesis, two KPIs anchor the scale (22% of sleep is REM, five cycles per night), an **actual hypnogram** as a step chart with pinned annotations shows deep sleep front-loading and REM lengthening later, and a comparison table lays out the four major theories with what each claims, its stage bias, its strongest evidence, and its open gap. Cream paper, serif type, ghost cards — the agent picked a "science essay" identity because that's what the question wanted.</sub>

---

## Architecture

The best way to explain the architecture is to let the system explain itself. This is a Vision canvas about Vision — same tools, same command layer, same palette adapter as any other canvas:

![Vision architecture, explained as a Vision canvas](screenshots/architecture.png)

<sub>Prompt: *"Explain the vision system's architecture — as a Vision canvas."* Hero states the thesis. Four KPIs anchor the scale (14 client-side tools, 11 endpoints, 9 widget kinds, 35+ chart types). Four narrative cards trace one turn through browser → runtime → model → canvas. The boundaries table names what each layer *can* do — the same read as "what it *cannot* do."</sub>

### The wire, one turn at a time

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser<br/>(useChat)
  participant F as FastAPI<br/>agent loop
  participant G as Grok 4.5<br/>via xai-sdk
  participant C as Command layer<br/>(Pydantic)
  participant P as Postgres<br/>canvas.*
  participant S as SSE hub<br/>per canvas

  B->>F: POST /api/chat  (messages, canvasId)
  F->>P: read canvas summary  (~1k tokens)
  F->>G: stream with tools + summary + prompt
  loop while there are tool calls
    G-->>F: text / reasoning / tool_call
    F-->>B: UI-message-stream part (SSE)
    F->>C: apply_change_set([op])
    C->>P: BEGIN → INSERT widget, placement, version → COMMIT
    C->>S: notify(canvasId)
    S-->>B: SSE "canvas_changed" (other tabs re-fetch)
    F-->>G: tool_result
  end
  F-->>B: [DONE]
```

The important edges: **Grok never touches Postgres.** Its outputs are typed tool calls that the command layer validates, transacts, and versions. Every mutation returns an inverse; the undo stack is the log.

### Stack

| Layer | Choice | Why |
|---|---|---|
| Model | xAI Grok 4.5 via `xai-sdk` | Streaming, server-side agent tools, structured `chat.parse()` |
| Server-side tools | `web_search`, `x_search`, `code_execution` | Live data + arithmetic without the model getting to write raw SQL |
| Images | Grok Imagine (`grok-imagine-image`) | Concept art, moods, diagrams-as-art |
| Backend | Python 3.12 + FastAPI + asyncpg | One async loop per turn; long-lived SSE connections |
| Schemas | Pydantic v2 | One definition drives the tool schema sent to Grok, the command validator, and the OpenAPI doc |
| Frontend | React + Vite + TypeScript | `useChat` from `@ai-sdk/react` for the streaming client |
| Layout | GridStack | Drag/resize emits typed operations through the same command layer the agent uses |
| Charts | Apache ECharts behind an adapter | Model sends a `VisualizationSpec`; the adapter picks colors from a validated palette |
| Persistence | PostgreSQL (`canvas.*`, `conversation.*`, `audit.*`) | Append-only versions and audit log |

## Edits are surgical

Every edit — from the agent, from GridStack, from a keyboard undo — flows through the same typed change-set machinery. Nothing rebuilds a canvas from scratch when a single field changes.

Watch the invariant hold. Start with the AI-dollar-flow canvas at version 12, Nvidia-green identity. Say *"change the visual identity — it should feel like a fire alarm, not a semiconductor spec sheet."*

<table><tr>
<td width="50%" align="center"><b>Before — v12, "Wafer Current"</b><br/><img src="screenshots/edit-before.png" width="100%"/></td>
<td width="50%" align="center"><b>After — v13, "Fire Alarm"</b><br/><img src="screenshots/edit-after.png" width="100%"/></td>
</tr></table>

One tool call. One database transaction. Every widget kept its ID, its version, its position, its data:

```text
Before:  canvas v12   style Wafer Current #76B900   12 widgets, all at v1
After:   canvas v13   style Fire Alarm    #E63900   12 widgets, all at v1  ← same IDs, same v1
History: +1 change set, 1 operation
```

The actual change set the runtime stored, straight from `GET /api/canvases/{id}/history`:

```json
{
  "origin": "agent",
  "status": "applied",
  "operations": [
    {"kind": "set_style", "style": {
      "name": "Fire Alarm",
      "accent": "#E63900",
      "type": "sans",
      "paper": "cream",
      "cards": "flat"
    }}
  ],
  "inverse": [
    {"kind": "set_style", "style": {
      "name": "Wafer Current",
      "accent": "#76B900",
      "type": "mono",
      "paper": "cool",
      "cards": "ghost"
    }}
  ]
}
```

`⌘Z` (or `POST /api/canvases/{id}/undo`) applies the `inverse` as a new change set: canvas v13 → v14, style back to Wafer Current, widgets *still* at v1, history *still* preserved. Undo is a step forward in the log, never a delete.

The invariant this preserves — from [`server/app/commands.py`](server/app/commands.py):

```mermaid
flowchart LR
  A[Tool call] -->|typed op| B{Validate}
  B -->|reject| E[Return errors<br/>to model]
  B -->|accept| T[BEGIN tx]
  T --> V[Snapshot prev version]
  V --> W[Apply mutation]
  W --> I[Store inverse op]
  I --> Bump[Bump canvas version]
  Bump --> Log[Append audit event]
  Log --> C[COMMIT]
  C --> N[SSE notify]
```

The naive alternative — "regenerate the canvas from a new prompt" — would create 12 new widgets, 12 new IDs, orphan the previous version, and break any UI state (scroll position, selection, filter) already attached to the old widgets. That's not editing. That's replacing.

## How it works

## The invariants

Four things that hold regardless of the prompt, the model, or the widget kind:

- **The agent proposes; the deterministic layer decides.** Grok never writes to the database. It calls 14 typed tools (`add_chart`, `add_kpi`, `add_table`, `add_narrative`, `add_statement`, `add_hero`, `add_label`, `add_control`, `generate_image`, `update_widget`, `remove_widget`, `set_layout`, `set_lanes`, `set_style`) whose inputs are Pydantic models. Each call becomes a change set that [`server/app/commands.py`](server/app/commands.py) validates, transacts, and versions.
- **Charts are data, not code.** The model sends a typed `VisualizationSpec` — chart type, axes, series, optional annotations. [`web/src/chartAdapter.ts`](web/src/chartAdapter.ts) translates that into ECharts options using a validated colorblind-safe palette. The model cannot pick colors, emit renderer config, or ship executable code.
- **Every widget carries provenance.** Source, as-of date, and a confidence of `measured` / `estimated` / `illustrative`, rendered as a footer with a status dot. Invented numbers are labelled as invented.
- **Layout is bidirectional.** Dragging or resizing a widget emits `move_widget` / `resize_widget` operations through the same command layer the agent uses, so direct manipulation and agent edits share one code path, one version history, and one undo stack (`⌘Z`).

Plus one design decision that changes the feel: **the agent designs each canvas.** Before building, the model calls `set_style` — an accent hex drawn from the subject, a typographic voice (serif, mono, sans), a paper tint, and a card treatment. Two canvases about different subjects never share an identity.

## Streaming

`POST /api/chat` speaks the AI SDK UI message stream protocol ([`server/app/uistream.py`](server/app/uistream.py)), so the React `useChat` client gets text, reasoning, and tool parts as they happen while the backend stays Python. A separate SSE channel per canvas pushes change notifications, so widgets appear the instant a command commits rather than when the turn ends — and every other tab open on the same canvas converges automatically.

## API surface

Eleven endpoints. Two are streams; nine are plain JSON. FastAPI serves the
docs at [`/docs`](http://localhost:3001/docs).

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/health` | Model id and OK check |
| `GET`  | `/api/canvases` | List canvases, newest first |
| `POST` | `/api/canvases` | Create a canvas |
| `GET`  | `/api/canvases/{id}` | Full state: meta, style, widgets, placements |
| `GET`  | `/api/canvases/{id}/messages` | Persisted conversation |
| `GET`  | `/api/canvases/{id}/events` | **SSE.** `canvas_changed` pings |
| `POST` | `/api/canvases/{id}/commands` | Apply a change set (agent + direct manipulation share this) |
| `POST` | `/api/canvases/{id}/compact` | Push overlapping placements down |
| `POST` | `/api/canvases/{id}/undo` | Apply the last change set's inverse |
| `GET`  | `/api/canvases/{id}/history` | Change-set log for this canvas |
| `POST` | `/api/chat` | **SSE.** One agent turn, streams the AI SDK UI message protocol |

The five typed operations `/commands` accepts are `add_widget`,
`update_widget`, `remove_widget`, `move_widget`, `resize_widget`. Same set for
the agent and for GridStack.

## Run

```bash
cp .env.example .env   # put your XAI_API_KEY in .env
```

```bash
docker compose up --build
```

Open [http://localhost:5173](http://localhost:5173). Postgres is published on
host port **5433** to avoid clashing with a local install.

To run the app processes directly against the containerised database:

```bash
docker compose up -d postgres
```

```bash
cd server && pip install -r requirements.txt && \
  DATABASE_URL=postgres://vision:vision@localhost:5433/vision \
  uvicorn app.main:app --port 3001 --reload
```

```bash
cd web && npm install && npm run dev
```

### Take your own screenshots

The Playwright script in `web/scripts/shoot.mjs` deep-links to a canvas by
title match, hides the chrome, and saves a full-board PNG.

```bash
cd web && node scripts/shoot.mjs ../screenshots "ARR vs cash" "Coffee"
```

## Keyboard

| Key | Action |
|---|---|
| `⌘K` | Focus the command bar |
| `⌘Z` | Undo the last change set |
| `Enter` | Send · `Shift+Enter` newline |

## Relationship to the architecture plan

[`canvas-technical-architecture.md`](canvas-technical-architecture.md) is the
full enterprise design — the version with entitlements, sharing, scheduled
refresh, exports, warehouse connectors, and the LangGraph agent graph with
approval tokens for risky change sets. This prototype implements its
load-bearing ideas: artifact/placement split, typed change sets with inverse
operations, append-only versions and audit, the spec→renderer boundary,
server-generated GridStack placements, SSE progress, provenance disclosure,
and the compact canvas summary handed to the agent each turn. It deliberately
omits the multi-tenant parts. If you want to know what the productionised
version looks like, read §7 of that document.

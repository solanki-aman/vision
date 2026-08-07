# Data provenance: separating facts from elements

**Status:** proposal · **Scope:** Phase 1–2 (data layer + enforcement). Phase 3 (per-number timeline UI) and Phase 4 (derived-fact lineage) are sketched at the end but not specified here.

## Problem

Today the model *is* the data layer. `web_search` returns a prose brief ([search.py:24](server/app/search.py)); the model reads it and **transcribes numbers by hand** into a widget `spec` ([tools.py:281](server/app/tools.py)); provenance is a single line **per widget** — `{source, asOf, confidence}` ([specs.py:253](server/app/specs.py)). Transcription is exactly where a wrong or invented number enters, and once it is in the spec there is no record of where it came from or whether it is even real.

We want the inverse: **the number is a first-class record with its own lineage, and a widget only references it.** The composing model arranges facts; it does not author their values.

## The guarantee (and its honest limit)

An LLM cannot be removed from *extraction* — pulling "$14.6B, FY2025" out of a web page is model work. So the guarantee is not "no LLM touches numbers." It is:

1. **Extraction is a separate step from composition.** A search turns results into structured **facts**, each pinned to a quoted source snippet. This is the only place a number is born.
2. **The composing model may not type a value — only reference a `factId`.** The command layer **resolves the stored value from the fact**, discarding any literal the model wrote. The model's number is a hint at most; the fact is authoritative.
3. **Every asserted number is checkable** against the snippet it came from, and **every unbacked number is rejected** at the write path — the same gate that already validates specs ([commands.py:131](server/app/commands.py)).

(2) + (3) are what make this structural rather than a convention.

## Data model

A new store, `canvas.facts`, keyed per canvas. A fact is **scalar** (a KPI number) or **series** (a retrieved vector — a time series, a ranked set — that shares one lineage). Series as a first-class unit is what keeps binding practical: a 30-point line references *one* fact, not 30.

```
canvas.facts
  id          UUID pk
  canvas_id   UUID -> canvas.canvases
  kind        TEXT   -- 'scalar' | 'series'
  entity      TEXT   -- the recurring cast member: 'Lam Research', 'BTC-USD' (drives the colour thread)
  label       TEXT   -- what it measures: 'FY25 revenue', 'daily close'
  unit        TEXT   -- '%', 'USD', 'USD M', null
  as_of       TEXT   -- the date the figure is stated as of
  value       DOUBLE PRECISION           -- scalar only
  points      JSONB  -- series only: [{x, y}], x = category/date
  tool        TEXT   -- 'web_search' | 'x_search' | 'code_execution' | 'user'
  query       TEXT   -- the search query that produced it
  snippet     TEXT   -- the quoted source text the number was read from
  source_url  TEXT
  confidence  TEXT   -- 'measured' | 'estimated' | 'illustrative'
  derived_from JSONB -- [factId]  (Phase 4)
  formula      TEXT  -- (Phase 4)
  created_at  TIMESTAMPTZ default now()
```

Additive migration only, matching the existing pattern ([db.py:155](server/app/db.py)): add the table in `SCHEMA`, and `ALTER TABLE canvas.widgets ADD COLUMN IF NOT EXISTS bindings JSONB`.

### Binding: how an element points at facts

A **provenance sidecar** on the widget, not a rewrite of the spec. Values stay inline so the renderer is unchanged; each is *bound* by a JSON path into the spec:

```
widget.bindings = [
  { path: "value",          factId: "…" },   // KpiSpec.value  (scalar fact)
  { path: "series[0]",      factId: "…" },   // ChartSpec series -> a series fact
  { path: "lines[2].value", factId: "…" },   // StatementLine.value
]
```

At the write path, `bind_and_materialize(spec, bindings, confidence)` runs **after** `validate_spec`:

- For each binding, load the fact and **write its canonical value(s) into the spec** at `path`. For a `series` binding it fills `series[N].data` from `fact.points` (and can fill `xAxis.categories` from the points' `x`), so the model never hand-types the array.
- **Enumerate every data-bearing numeric leaf** in the materialized spec (`series[].data`, `kpi.value`/`baseline`/`sparkline`, `statement.lines[].value`, numeric `table.rows` cells, `target`, `links[].value`, `hierarchy[].value`, `ohlc`, `boxes`, `calendar[].value`). Each must be covered by a binding. Any uncovered numeric leaf on a widget **not** declared `illustrative` → reject, with a message the model can act on: *"series[0].data has no fact binding — search for it or mark the widget illustrative."*

Numbers the **UI itself computes** (e.g. `KpiComparison` percent change — the UI already derives it) need no binding. Widgets explicitly marked `confidence: illustrative` bypass binding entirely — concept sketches stay honest and cheap.

### Text and claims

Numbers in structured fields get hard bindings (above). A **title or narrative is a claim, not a retrieved fact** — "Freight cost doubled while volume grew 9%." It gets a softer, widget-level link:

```
widget.bindings += [{ path: "$claim", factIds: ["…","…"] }]  // the facts the claim rests on
```

Parsing numbers *embedded in prose* down to individual facts is deferred (Phase 3+). **Open decision:** is widget-level `authoredFrom` enough for text at first, or do you want inline number-in-prose provenance from the start?

## Phase 1 — structured search → facts

Change `run_search` ([search.py:24](server/app/search.py)) to ask for **structured extraction** instead of a brief: for the figures that answer the question, return `{entity, label, value|points, unit, as_of, snippet, source_url}` as JSON. Persist them to `canvas.facts` immediately (they exist before any widget), and return `{facts: [{factId, entity, label, value|points, unit, as_of, source_url}], summary}` to the model.

- `web_search` tool ([tools.py:401](server/app/tools.py)) now hands the model a list of `factId`s it can reference.
- `get_canvas_summary` ([db.py:233](server/app/db.py)) also lists **available facts** (id, entity, label, value, as_of) so later turns reuse existing facts instead of re-searching — this is also what makes "add SpaceX" reuse the same series and colour thread rather than spawn a detached tile.

No UI change in Phase 1. The payoff is that numbers are now stored with lineage even if widgets still embed them.

## Phase 2 — bind + enforce

1. **Schema:** `canvas.facts` + `widgets.bindings` ([db.py:45](server/app/db.py)).
2. **`bind_and_materialize`** in the command layer, called from `add_widget`, `update_widget`, and `add_chart_series` in `apply_change_set` ([commands.py:129](server/app/commands.py)) right after `validate_spec`. Resolve values from facts; reject unbacked numbers.
3. **Tool args:** every `create_*`/`update_*`/`add_chart_series` accepts an optional `bindings` list; the model passes `factId`s instead of (or alongside) raw values.
4. **Prompt:** a new SKILL.md section — *"Data is bound, not typed. You may not write a number you did not get from `web_search` (or the user, or `code_execution`). Reference its `factId`. Need a number you don't have? Search first."* This replaces the current honour-system framing at [SKILL.md:26](server/app/skills/viz-gen/SKILL.md).
5. **Legacy widgets** (null `bindings`) render as today but are badged **unverified** — no backfill required.

## File-by-file

| File | Change |
|---|---|
| `server/app/db.py` | `canvas.facts` table; `widgets.bindings` column; `record_facts()`; facts in `get_canvas_summary` |
| `server/app/search.py` | structured extraction; persist facts; return factIds |
| `server/app/specs.py` | `Fact` / `Binding` models; helper to enumerate numeric leaves |
| `server/app/commands.py` | `bind_and_materialize`; call it in the three widget ops; store `bindings` |
| `server/app/tools.py` | `bindings` arg on create/update/add_series; `web_search` returns facts |
| `server/app/skills/viz-gen/SKILL.md` | "Data is bound, not typed"; retire the honour-system wording |
| `web/src/types.ts`, `Canvas.tsx`, `WidgetBody.tsx` | read `bindings`; "unverified" badge (the (i) timeline is Phase 3) |

## Open decisions

1. **Value authority** — command layer *overwrites* inline values from facts (recommended, strongest) vs *only validates* they match. I recommend overwrite.
2. **Series-fact as the unit** for vectors (recommended) vs per-point scalar facts (precise but heavy).
3. **Text provenance** — widget-level `authoredFrom` now, inline-in-prose later (recommended) vs inline from day one.
4. **Derived numbers** (growth %, ratios) — Phase 2 can either *block* them (must come from `code_execution`) or allow with `confidence: estimated` + an `authoredFrom` link until Phase 4 wires real lineage. I lean allow-with-estimated so we don't gate Phase 2 on Phase 4.

## Later (not specified here)

- **Phase 3 — the (i) timeline.** A per-number affordance in `WidgetBody` opening a popover: tool, query, snippet, source + as_of, confidence. Reads the bound fact's lineage.
- **Phase 4 — derived-fact lineage.** Math moves into `code_execution`; `derived_from` + `formula` make the timeline a small lineage tree (retrieved facts are leaves, computed facts are nodes). Improves correctness, not just traceability.

import { tool } from "ai";
import { z } from "zod";
import { chartSpec, kpiSpec, tableSpec, narrativeSpec, provenance } from "./specs.js";
import { applyChangeSet, compact, type Operation } from "./commands.js";
import { generateImage } from "./imagine.js";

const size = z
  .object({
    w: z.number().int().min(2).max(12).describe("Width in columns of a 12-column grid."),
    h: z.number().int().min(2).max(9).describe("Height in rows; each row is about 76px."),
  })
  .optional()
  .describe("You own the layout. Omit only when the default size is genuinely right.");

const titled = {
  title: z.string().describe("A title that states the finding, not the topic."),
  provenance,
  size,
};

export function buildTools(canvasId: string, onChange: () => void) {
  const place = async (op: Operation) => {
    const result = await applyChangeSet(canvasId, [op], "agent");
    onChange();
    if (result.errors.length) return { ok: false, errors: result.errors };
    return { ok: true, widgetId: result.applied[0]?.widgetId };
  };

  return {
    add_chart: tool({
      description:
        "Place a chart on the canvas. Your primary tool — reach for it first.",
      inputSchema: z.object({ ...titled, spec: chartSpec }),
      execute: async ({ title, spec, provenance, size }) =>
        place({ kind: "add_widget", widgetKind: "chart", title, spec, provenance, size }),
    }),

    add_kpi: tool({
      description:
        "Place a single decisive number with an optional comparison and sparkline. Use for headline metrics instead of stating them in text.",
      inputSchema: z.object({ ...titled, spec: kpiSpec }),
      execute: async ({ title, spec, provenance, size }) =>
        place({ kind: "add_widget", widgetKind: "kpi", title, spec, provenance, size }),
    }),

    add_table: tool({
      description: "Place an exact-values table. Use when precision matters more than shape.",
      inputSchema: z.object({ ...titled, spec: tableSpec }),
      execute: async ({ title, spec, provenance, size }) =>
        place({ kind: "add_widget", widgetKind: "table", title, spec, provenance, size }),
    }),

    add_narrative: tool({
      description:
        "Place a short annotation card — the 'so what', a caveat, or a recommendation. This is where prose belongs.",
      inputSchema: z.object({ ...titled, spec: narrativeSpec }),
      execute: async ({ title, spec, provenance, size }) =>
        place({ kind: "add_widget", widgetKind: "narrative", title, spec, provenance, size }),
    }),

    generate_image: tool({
      description:
        "Generate an image with Grok Imagine and place it on the canvas. Use for concepts, moods, diagrams-as-art, or anything better shown than plotted.",
      inputSchema: z.object({
        title: z.string(),
        prompt: z.string().describe("Detailed visual prompt. Describe style, composition, palette."),
        quality: z.boolean().optional().describe("Slower, higher fidelity."),
        size,
      }),
      execute: async ({ title, prompt, quality, size }) => {
        const url = await generateImage(prompt, quality);
        return place({
          kind: "add_widget",
          widgetKind: "image",
          title,
          spec: { url, prompt },
          provenance: { source: "Grok Imagine", confidence: "illustrative" },
          size,
        });
      },
    }),

    update_widget: tool({
      description:
        "Revise a widget already on the canvas by id. Prefer this over adding a near-duplicate when the user asks for a change.",
      inputSchema: z.object({
        widgetId: z.string(),
        title: z.string().optional(),
        spec: z.unknown().optional().describe("Full replacement spec matching the widget's kind."),
      }),
      execute: async ({ widgetId, title, spec }) =>
        place({ kind: "update_widget", widgetId, title, spec }),
    }),

    arrange_canvas: tool({
      description:
        "Reposition and resize widgets already on the canvas. Use this to compose a deliberate layout — a KPI row across the top, charts beneath, notes to the side.",
      inputSchema: z.object({
        placements: z
          .array(
            z.object({
              widgetId: z.string(),
              x: z.number().int().min(0).max(11).describe("Left column, 0-11."),
              y: z.number().int().min(0).describe("Row from the top, 0 is the first row."),
              w: z.number().int().min(2).max(12),
              h: z.number().int().min(2).max(9),
            }),
          )
          .max(24),
      }),
      execute: async ({ placements }) => {
        const ops: Operation[] = placements.flatMap((p) => [
          { kind: "resize_widget" as const, widgetId: p.widgetId, w: p.w, h: p.h },
          { kind: "move_widget" as const, widgetId: p.widgetId, x: p.x, y: p.y },
        ]);
        const result = await applyChangeSet(canvasId, ops, "agent");
        await compact(canvasId);
        onChange();
        return { ok: result.errors.length === 0, errors: result.errors, moved: placements.length };
      },
    }),

    remove_widget: tool({
      description: "Take a widget off the canvas. Reversible via undo.",
      inputSchema: z.object({ widgetId: z.string() }),
      execute: async ({ widgetId }) => place({ kind: "remove_widget", widgetId }),
    }),
  };
}

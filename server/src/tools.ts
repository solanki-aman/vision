import { tool } from "ai";
import { z } from "zod";
import { chartSpec, kpiSpec, tableSpec, narrativeSpec, controlSpec, provenance } from "./specs.js";
import { applyChangeSet, applyLayout, type Operation } from "./commands.js";
import { generateImage } from "./imagine.js";

const HEIGHT_ROWS = { kpi: 4, short: 6, standard: 8, tall: 11 } as const;

const size = z
  .object({
    span: z.number().int().min(2).max(12).describe("Width in columns of a 12-column row."),
    height: z
      .enum(["kpi", "short", "standard", "tall"])
      .describe("kpi=120px metric tile, short=200px, standard=320px chart, tall=440px."),
  })
  .optional()
  .describe("Omit only when the default size is genuinely right; prefer set_layout at the end.");

const toSize = (s?: { span: number; height: keyof typeof HEIGHT_ROWS }) =>
  s ? { w: s.span, h: HEIGHT_ROWS[s.height] } : undefined;

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
        place({ kind: "add_widget", widgetKind: "chart", title, spec, provenance, size: toSize(size) }),
    }),

    add_kpi: tool({
      description:
        "Place a single decisive number with an optional comparison and sparkline. Use for headline metrics instead of stating them in text.",
      inputSchema: z.object({ ...titled, spec: kpiSpec }),
      execute: async ({ title, spec, provenance, size }) =>
        place({ kind: "add_widget", widgetKind: "kpi", title, spec, provenance, size: toSize(size) }),
    }),

    add_table: tool({
      description: "Place an exact-values table. Use when precision matters more than shape.",
      inputSchema: z.object({ ...titled, spec: tableSpec }),
      execute: async ({ title, spec, provenance, size }) =>
        place({ kind: "add_widget", widgetKind: "table", title, spec, provenance, size: toSize(size) }),
    }),

    add_narrative: tool({
      description:
        "Place a short annotation card — the 'so what', a caveat, or a recommendation. This is where prose belongs.",
      inputSchema: z.object({ ...titled, spec: narrativeSpec }),
      execute: async ({ title, spec, provenance, size }) =>
        place({ kind: "add_widget", widgetKind: "narrative", title, spec, provenance, size: toSize(size) }),
    }),

    add_control: tool({
      description:
        "Place a draggable range slider that filters other chart widgets on the canvas. Call this AFTER the charts exist, passing their widget ids as targets. Use it when a dashboard has a shared time axis the user will want to narrow.",
      inputSchema: z.object({ title: z.string(), spec: controlSpec, size }),
      execute: async ({ title, spec, size }) =>
        place({
          kind: "add_widget",
          widgetKind: "control",
          title,
          spec,
          provenance: { source: "Canvas control", confidence: "measured" },
          size: toSize(size),
        }),
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
          size: toSize(size),
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

    set_layout: tool({
      description:
        "Arrange the whole canvas as a dashboard. Pass an ordered list of rows; every card in a row shares one height and the spans are fitted to exactly 12 columns. Call this once, last, after all widgets exist. This is how a canvas stops looking like a pile of boxes.",
      inputSchema: z.object({
        rows: z
          .array(
            z.object({
              height: z
                .enum(["kpi", "short", "standard", "tall"])
                .describe("kpi for a metric strip, standard for most chart rows, tall for dense forms."),
              items: z
                .array(
                  z.object({
                    widgetId: z.string(),
                    span: z
                      .number()
                      .int()
                      .min(2)
                      .max(12)
                      .describe("Relative width. Spans in a row should sum to 12."),
                  }),
                )
                .min(1)
                .max(4)
                .describe("At most four cards per row, in left-to-right order."),
            }),
          )
          .min(1)
          .max(12),
      }),
      execute: async ({ rows }) => {
        const result = await applyLayout(canvasId, rows);
        onChange();
        return result;
      },
    }),

    remove_widget: tool({
      description: "Take a widget off the canvas. Reversible via undo.",
      inputSchema: z.object({ widgetId: z.string() }),
      execute: async ({ widgetId }) => place({ kind: "remove_widget", widgetId }),
    }),
  };
}

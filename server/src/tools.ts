import { tool } from "ai";
import { z } from "zod";
import { chartSpec, kpiSpec, tableSpec, narrativeSpec, provenance } from "./specs.js";
import { applyChangeSet, type Operation } from "./commands.js";
import { generateImage } from "./imagine.js";

const titled = {
  title: z.string().describe("A title that states the finding, not the topic."),
  provenance,
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
      execute: async ({ title, spec, provenance }) =>
        place({ kind: "add_widget", widgetKind: "chart", title, spec, provenance }),
    }),

    add_kpi: tool({
      description:
        "Place a single decisive number with an optional comparison and sparkline. Use for headline metrics instead of stating them in text.",
      inputSchema: z.object({ ...titled, spec: kpiSpec }),
      execute: async ({ title, spec, provenance }) =>
        place({ kind: "add_widget", widgetKind: "kpi", title, spec, provenance }),
    }),

    add_table: tool({
      description: "Place an exact-values table. Use when precision matters more than shape.",
      inputSchema: z.object({ ...titled, spec: tableSpec }),
      execute: async ({ title, spec, provenance }) =>
        place({ kind: "add_widget", widgetKind: "table", title, spec, provenance }),
    }),

    add_narrative: tool({
      description:
        "Place a short annotation card — the 'so what', a caveat, or a recommendation. This is where prose belongs.",
      inputSchema: z.object({ ...titled, spec: narrativeSpec }),
      execute: async ({ title, spec, provenance }) =>
        place({ kind: "add_widget", widgetKind: "narrative", title, spec, provenance }),
    }),

    generate_image: tool({
      description:
        "Generate an image with Grok Imagine and place it on the canvas. Use for concepts, moods, diagrams-as-art, or anything better shown than plotted.",
      inputSchema: z.object({
        title: z.string(),
        prompt: z.string().describe("Detailed visual prompt. Describe style, composition, palette."),
        quality: z.boolean().optional().describe("Slower, higher fidelity."),
      }),
      execute: async ({ title, prompt, quality }) => {
        const url = await generateImage(prompt, quality);
        return place({
          kind: "add_widget",
          widgetKind: "image",
          title,
          spec: { url, prompt },
          provenance: { source: "Grok Imagine", confidence: "illustrative" },
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

    remove_widget: tool({
      description: "Take a widget off the canvas. Reversible via undo.",
      inputSchema: z.object({ widgetId: z.string() }),
      execute: async ({ widgetId }) => place({ kind: "remove_widget", widgetId }),
    }),
  };
}

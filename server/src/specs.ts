import { z } from "zod";

export const chartSpec = z.object({
  chartType: z.enum([
    "line",
    "area",
    "bar",
    "horizontal_bar",
    "stacked_bar",
    "pie",
    "donut",
    "scatter",
    "radar",
    "heatmap",
    "waterfall",
    "gauge",
    "sankey",
  ]),
  xAxis: z
    .object({ label: z.string().optional(), categories: z.array(z.string()) })
    .optional(),
  yAxis: z
    .object({ label: z.string().optional(), unit: z.string().optional() })
    .optional(),
  series: z.array(
    z.object({
      name: z.string(),
      data: z.array(z.number().nullable()),
    }),
  ),
  links: z
    .array(z.object({ from: z.string(), to: z.string(), value: z.number() }))
    .optional()
    .describe("Sankey only: flows between node names."),
});

export const kpiSpec = z.object({
  value: z.number(),
  unit: z.string().optional().describe("Unit of `value`, e.g. '%', 'M', 'USD'."),
  label: z.string().describe("What the number measures, e.g. 'million people'."),
  comparison: z
    .object({
      baseline: z
        .number()
        .describe("The reference value to compare against, in the SAME unit as `value`. The UI computes the percent change itself."),
      label: z.string().describe("Short name of the baseline only, e.g. 'China' or 'last quarter'. Not a sentence."),
      favorableDirection: z
        .enum(["up", "down", "neutral"])
        .describe("Which direction is good for this metric. Use 'neutral' when neither is."),
    })
    .optional()
    .describe("Omit entirely unless there is a genuine like-for-like baseline."),
  sparkline: z.array(z.number()).optional().describe("Recent values of this same metric, oldest first."),
});

export const tableSpec = z.object({
  columns: z.array(
    z.object({
      key: z.string(),
      label: z.string(),
      align: z.enum(["left", "right"]).optional(),
    }),
  ),
  rows: z.array(z.record(z.string(), z.union([z.string(), z.number(), z.null()]))),
});

export const narrativeSpec = z.object({
  body: z.string().describe("2-5 short sentences. Markdown-free plain text."),
  bullets: z.array(z.string()).max(5).optional(),
  tone: z.enum(["neutral", "positive", "caution", "critical"]).default("neutral"),
});

export const imageSpec = z.object({
  url: z.string(),
  prompt: z.string(),
});

export const provenance = z.object({
  source: z.string().describe("Where the numbers came from, e.g. 'Live web search' or 'Illustrative'"),
  asOf: z.string().optional(),
  confidence: z.enum(["measured", "estimated", "illustrative"]),
  note: z.string().optional(),
});

export const WIDGET_KINDS = ["chart", "kpi", "table", "narrative", "image"] as const;
export type WidgetKind = (typeof WIDGET_KINDS)[number];

const specByKind = {
  chart: chartSpec,
  kpi: kpiSpec,
  table: tableSpec,
  narrative: narrativeSpec,
  image: imageSpec,
};

export function validateSpec(kind: WidgetKind, spec: unknown) {
  return specByKind[kind].safeParse(spec);
}

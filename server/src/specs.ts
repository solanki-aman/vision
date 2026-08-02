import { z } from "zod";

export const CHART_TYPES = [
  // over time / across categories — needs xAxis.categories + series
  "line", "area", "stacked_area", "step_line", "bar", "horizontal_bar",
  "stacked_bar", "stacked_horizontal_bar", "pictorial_bar", "scatter",
  "effect_scatter", "bubble", "waterfall", "theme_river",
  // part of a whole — needs xAxis.categories + one series
  "pie", "donut", "rose", "funnel", "gauge",
  // hierarchy — needs `hierarchy`
  "treemap", "sunburst", "tree",
  // relationships — needs `links`
  "sankey", "graph", "chord",
  // distributions and matrices
  "boxplot", "candlestick", "heatmap", "calendar", "radar", "parallel",
] as const;

export const chartSpec = z.object({
  chartType: z.enum(CHART_TYPES),
  xAxis: z
    .object({ label: z.string().optional(), categories: z.array(z.string()) })
    .optional()
    .describe("Category or time axis. Required for cartesian, part-of-whole, radar and heatmap types."),
  yAxis: z
    .object({ label: z.string().optional(), unit: z.string().optional() })
    .optional(),
  series: z
    .array(z.object({ name: z.string(), data: z.array(z.number().nullable()) }))
    .default([])
    .describe(
      "One entry per line/bar/slice group, aligned to xAxis.categories. For heatmap, one entry per row. For bubble, the second series carries point sizes.",
    ),
  links: z
    .array(z.object({ from: z.string(), to: z.string(), value: z.number() }))
    .optional()
    .describe("sankey, graph and chord: weighted connections between node names."),
  hierarchy: z
    .array(
      z.object({
        name: z.string(),
        parent: z.string().optional().describe("Omit for root nodes."),
        value: z.number().optional().describe("Leaf size. Parents are summed when omitted."),
      }),
    )
    .optional()
    .describe("treemap, sunburst and tree: a flat parent-child list, not a nested object."),
  ohlc: z
    .array(
      z.object({
        date: z.string(),
        open: z.number(),
        high: z.number(),
        low: z.number(),
        close: z.number(),
      }),
    )
    .optional()
    .describe("candlestick only."),
  boxes: z
    .array(
      z.object({
        name: z.string(),
        min: z.number(),
        q1: z.number(),
        median: z.number(),
        q3: z.number(),
        max: z.number(),
      }),
    )
    .optional()
    .describe("boxplot only: five-number summary per group."),
  calendar: z
    .array(z.object({ date: z.string().describe("YYYY-MM-DD"), value: z.number() }))
    .optional()
    .describe("calendar only: one entry per day."),
  zoom: z
    .boolean()
    .optional()
    .describe("Adds a draggable range slider under the chart. Use for long time series (20+ points)."),
});

export const controlSpec = z.object({
  control: z.literal("range").describe("A draggable range slider."),
  label: z.string().describe("What the slider filters, e.g. 'Period' or 'Months shown'."),
  targets: z
    .array(z.string())
    .min(1)
    .describe("Widget ids this slider filters. Chart widgets only; they must share an x-axis."),
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

export const WIDGET_KINDS = ["chart", "kpi", "table", "narrative", "image", "control"] as const;
export type WidgetKind = (typeof WIDGET_KINDS)[number];

const specByKind = {
  chart: chartSpec,
  kpi: kpiSpec,
  table: tableSpec,
  narrative: narrativeSpec,
  image: imageSpec,
  control: controlSpec,
};

export function validateSpec(kind: WidgetKind, spec: unknown) {
  return specByKind[kind].safeParse(spec);
}

import { tool } from "ai";
import { z } from "zod";

// Typed visualization spec — the model never emits executable code or raw
// ECharts options. The web app's adapter translates this into an ECharts
// option object (mirrors the VisualizationSpec → adapter boundary in the
// architecture doc).
export const chartSpecSchema = z.object({
  title: z.string().describe("Short chart title"),
  takeaway: z
    .string()
    .describe("One-sentence insight the chart demonstrates"),
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
  ]),
  xAxis: z
    .object({
      label: z.string().optional(),
      categories: z.array(z.string()),
    })
    .optional()
    .describe("Category axis. Required for all types except pie/donut/radar."),
  yAxis: z
    .object({
      label: z.string().optional(),
      unit: z.string().optional().describe("e.g. USD, %, users"),
    })
    .optional(),
  series: z
    .array(
      z.object({
        name: z.string(),
        data: z.array(z.number().nullable()),
      }),
    )
    .describe(
      "For pie/donut: one series whose data aligns with xAxis.categories. For heatmap: one series per row.",
    ),
});

export type ChartSpec = z.infer<typeof chartSpecSchema>;

export const tools = {
  render_chart: tool({
    description:
      "Render an interactive chart for the user. This is your PRIMARY way of communicating. Call it one or more times in every response.",
    inputSchema: chartSpecSchema,
    execute: async (spec: ChartSpec) => ({
      rendered: true,
      title: spec.title,
    }),
  }),
};

export const SYSTEM_PROMPT = `You are Vision, an analyst agent that ALWAYS communicates through visualizations.

Hard rules:
- Every response MUST include at least one render_chart tool call. Charts are the answer; words are only a brief preamble or reasoning.
- Keep any text to 1-3 short sentences before or between charts — never a long prose answer.
- If the user asks something with no inherent data (e.g. "hello"), still respond visually: chart something playful or illustrative about the topic.
- If the user provides data, chart it faithfully. If they ask a conceptual question, construct reasonable illustrative data and say it is illustrative.
- Prefer multiple small focused charts over one overloaded chart.
- Pie/donut only for true part-of-whole with at most 6 slices.
- Always fill "takeaway" with the single insight the chart shows.`;

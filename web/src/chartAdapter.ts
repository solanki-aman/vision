import type { EChartsOption, SeriesOption } from "echarts";
import { SERIES_COLORS, SEQUENTIAL_RAMP, INK } from "./theme";

export interface ChartSpec {
  title: string;
  takeaway: string;
  chartType:
    | "line"
    | "area"
    | "bar"
    | "horizontal_bar"
    | "stacked_bar"
    | "pie"
    | "donut"
    | "scatter"
    | "radar"
    | "heatmap"
    | "waterfall";
  xAxis?: { label?: string; categories: string[] };
  yAxis?: { label?: string; unit?: string };
  series: { name: string; data: (number | null)[] }[];
}

const AXIS_STYLE = {
  axisLine: { lineStyle: { color: INK.axis } },
  axisLabel: { color: INK.muted },
  splitLine: { lineStyle: { color: INK.grid } },
  nameTextStyle: { color: INK.secondary },
};

// Deterministic adapter: typed spec in, ECharts option out. The model never
// supplies renderer options directly.
export function specToOption(spec: ChartSpec): EChartsOption {
  const categories = spec.xAxis?.categories ?? [];
  const unit = spec.yAxis?.unit ? ` ${spec.yAxis.unit}` : "";

  const base: EChartsOption = {
    color: SERIES_COLORS,
    backgroundColor: "transparent",
    textStyle: { color: INK.secondary, fontFamily: "system-ui, sans-serif" },
    tooltip: {
      trigger: ["pie", "donut", "scatter", "heatmap"].includes(spec.chartType)
        ? "item"
        : "axis",
      backgroundColor: INK.surface,
      borderColor: INK.border,
      textStyle: { color: INK.primary },
      valueFormatter: (v: unknown) =>
        typeof v === "number" ? `${v.toLocaleString()}${unit}` : String(v ?? ""),
    },
    legend:
      spec.series.length > 1 && !["heatmap"].includes(spec.chartType)
        ? { bottom: 0, textStyle: { color: INK.secondary } }
        : undefined,
    grid: { left: 48, right: 24, top: 24, bottom: spec.series.length > 1 ? 56 : 32 },
  };

  switch (spec.chartType) {
    case "line":
    case "area":
      return {
        ...base,
        xAxis: { type: "category", data: categories, ...AXIS_STYLE },
        yAxis: { type: "value", name: spec.yAxis?.label, ...AXIS_STYLE },
        series: spec.series.map((s) => ({
          type: "line",
          name: s.name,
          data: s.data,
          smooth: false,
          symbolSize: 7,
          lineStyle: { width: 2 },
          areaStyle: spec.chartType === "area" ? { opacity: 0.18 } : undefined,
        })) as SeriesOption[],
      };

    case "bar":
    case "stacked_bar":
      return {
        ...base,
        xAxis: { type: "category", data: categories, ...AXIS_STYLE },
        yAxis: { type: "value", name: spec.yAxis?.label, ...AXIS_STYLE },
        series: spec.series.map((s) => ({
          type: "bar",
          name: s.name,
          data: s.data,
          stack: spec.chartType === "stacked_bar" ? "total" : undefined,
          itemStyle: { borderRadius: [4, 4, 0, 0] },
          barGap: "10%",
        })) as SeriesOption[],
      };

    case "horizontal_bar":
    case "waterfall": // rendered as ranked horizontal bars for the prototype
      return {
        ...base,
        grid: { left: 110, right: 32, top: 16, bottom: 32 },
        xAxis: { type: "value", name: spec.yAxis?.label, ...AXIS_STYLE },
        yAxis: {
          type: "category",
          data: [...categories].reverse(),
          ...AXIS_STYLE,
        },
        series: spec.series.map((s) => ({
          type: "bar",
          name: s.name,
          data: [...s.data].reverse(),
          itemStyle: { borderRadius: [0, 4, 4, 0] },
        })) as SeriesOption[],
      };

    case "pie":
    case "donut": {
      const s = spec.series[0] ?? { name: "", data: [] };
      return {
        ...base,
        legend: { bottom: 0, textStyle: { color: INK.secondary } },
        series: [
          {
            type: "pie",
            name: s.name,
            radius: spec.chartType === "donut" ? ["45%", "72%"] : "72%",
            itemStyle: { borderColor: INK.surface, borderWidth: 2 },
            label: { color: INK.secondary },
            data: categories.map((c, i) => ({ name: c, value: s.data[i] ?? 0 })),
          },
        ],
      };
    }

    case "scatter":
      return {
        ...base,
        xAxis: {
          type: "category",
          data: categories,
          name: spec.xAxis?.label,
          ...AXIS_STYLE,
        },
        yAxis: { type: "value", name: spec.yAxis?.label, ...AXIS_STYLE },
        series: spec.series.slice(0, 3).map((s) => ({
          type: "scatter",
          name: s.name,
          data: s.data,
          symbolSize: 10,
        })) as SeriesOption[],
      };

    case "radar":
      return {
        ...base,
        radar: {
          indicator: categories.map((c) => ({ name: c })),
          axisName: { color: INK.secondary },
          splitLine: { lineStyle: { color: INK.grid } },
          axisLine: { lineStyle: { color: INK.axis } },
          splitArea: { show: false },
        },
        series: [
          {
            type: "radar",
            data: spec.series.map((s) => ({ name: s.name, value: s.data })),
          },
        ],
      };

    case "heatmap": {
      const data: [number, number, number][] = [];
      spec.series.forEach((row, y) =>
        row.data.forEach((v, x) => data.push([x, y, v ?? 0])),
      );
      const values = data.map((d) => d[2]);
      return {
        ...base,
        grid: { left: 110, right: 24, top: 16, bottom: 64 },
        xAxis: { type: "category", data: categories, ...AXIS_STYLE },
        yAxis: {
          type: "category",
          data: spec.series.map((s) => s.name),
          ...AXIS_STYLE,
        },
        visualMap: {
          min: Math.min(...values, 0),
          max: Math.max(...values, 1),
          orient: "horizontal",
          left: "center",
          bottom: 0,
          inRange: { color: SEQUENTIAL_RAMP },
          textStyle: { color: INK.secondary },
        },
        series: [
          {
            type: "heatmap",
            data,
            itemStyle: { borderColor: INK.surface, borderWidth: 2 },
            label: { show: true, color: INK.primary },
          },
        ],
      };
    }
  }
}

import type { EChartsOption, SeriesOption } from "echarts";
import { SERIES, SEQUENTIAL, INK } from "./theme";

export interface ChartSpec {
  chartType:
    | "line" | "area" | "bar" | "horizontal_bar" | "stacked_bar" | "pie" | "donut"
    | "scatter" | "radar" | "heatmap" | "waterfall" | "gauge" | "sankey";
  xAxis?: { label?: string; categories: string[] };
  yAxis?: { label?: string; unit?: string };
  series: { name: string; data: (number | null)[] }[];
  links?: { from: string; to: string; value: number }[];
}

const axis = {
  axisLine: { lineStyle: { color: INK.axis } },
  axisTick: { show: false },
  axisLabel: { color: INK.muted, fontSize: 11 },
  splitLine: { lineStyle: { color: INK.grid, width: 1 } },
  nameTextStyle: { color: INK.secondary, fontSize: 11 },
};

const ITEM_TRIGGER = ["pie", "donut", "scatter", "heatmap", "gauge", "sankey"];

const clip = (n: number) => (s: string) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

// Widgets are small and resizable; long series names must never spill out of the card.
const legendBase = {
  type: "scroll" as const,
  bottom: 0,
  icon: "roundRect",
  itemWidth: 8,
  itemHeight: 8,
  formatter: clip(22),
  textStyle: { color: INK.secondary, fontSize: 11 },
  pageTextStyle: { color: INK.muted, fontSize: 10 },
  pageIconColor: INK.secondary,
  pageIconInactiveColor: INK.axis,
  pageIconSize: 9,
};

/** Typed spec in, renderer options out. The model never supplies ECharts config. */
export function specToOption(spec: ChartSpec): EChartsOption {
  const cats = spec.xAxis?.categories ?? [];
  const unit = spec.yAxis?.unit ? ` ${spec.yAxis.unit}` : "";
  const multi = spec.series.length > 1;

  const base: EChartsOption = {
    color: SERIES,
    backgroundColor: "transparent",
    animationDuration: 600,
    animationEasing: "cubicOut",
    textStyle: { color: INK.secondary, fontFamily: "system-ui, sans-serif" },
    tooltip: {
      trigger: ITEM_TRIGGER.includes(spec.chartType) ? "item" : "axis",
      backgroundColor: "rgba(20,20,19,0.95)",
      borderColor: INK.border,
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: INK.primary, fontSize: 12 },
      axisPointer: { type: "line", lineStyle: { color: INK.axis } },
      valueFormatter: (v: unknown) =>
        typeof v === "number" ? `${v.toLocaleString()}${unit}` : String(v ?? ""),
    },
    legend: multi ? legendBase : undefined,
    grid: { left: 8, right: 16, top: 12, bottom: multi ? 30 : 4, containLabel: true },
  };

  switch (spec.chartType) {
    case "line":
    case "area":
      return {
        ...base,
        xAxis: { type: "category", data: cats, boundaryGap: false, ...axis },
        yAxis: { type: "value", name: spec.yAxis?.label, ...axis },
        series: spec.series.map((s, i) => ({
          type: "line",
          name: s.name,
          data: s.data,
          symbolSize: 7,
          showSymbol: s.data.length <= 24,
          lineStyle: { width: 2 },
          areaStyle:
            spec.chartType === "area"
              ? { opacity: 0.16, color: SERIES[i % SERIES.length] }
              : undefined,
        })) as SeriesOption[],
      };

    case "bar":
    case "stacked_bar":
      return {
        ...base,
        xAxis: { type: "category", data: cats, ...axis },
        yAxis: { type: "value", name: spec.yAxis?.label, ...axis },
        series: spec.series.map((s) => ({
          type: "bar",
          name: s.name,
          data: s.data,
          stack: spec.chartType === "stacked_bar" ? "total" : undefined,
          itemStyle: { borderRadius: [4, 4, 0, 0] },
          barMaxWidth: 48,
        })) as SeriesOption[],
      };

    case "horizontal_bar":
      return {
        ...base,
        grid: { left: 8, right: 20, top: 8, bottom: multi ? 34 : 10, containLabel: true },
        xAxis: {
          type: "value",
          name: spec.yAxis?.label,
          nameLocation: "middle",
          nameGap: 26,
          ...axis,
        },
        yAxis: { type: "category", data: [...cats].reverse(), ...axis },
        series: spec.series.map((s) => ({
          type: "bar",
          name: s.name,
          data: [...s.data].reverse(),
          itemStyle: { borderRadius: [0, 4, 4, 0] },
          barMaxWidth: 28,
        })) as SeriesOption[],
      };

    case "waterfall": {
      const values = (spec.series[0]?.data ?? []).map((v) => v ?? 0);
      const support: number[] = [];
      let run = 0;
      for (const v of values) {
        support.push(v >= 0 ? run : run + v);
        run += v;
      }
      return {
        ...base,
        legend: undefined,
        xAxis: { type: "category", data: cats, ...axis },
        yAxis: { type: "value", name: spec.yAxis?.label, ...axis },
        series: [
          { type: "bar", stack: "wf", itemStyle: { color: "transparent" }, data: support, silent: true },
          {
            type: "bar",
            stack: "wf",
            data: values.map((v) => ({
              value: Math.abs(v),
              itemStyle: { color: v >= 0 ? SERIES[2] : SERIES[7], borderRadius: 3 },
            })),
            barMaxWidth: 48,
          },
        ] as SeriesOption[],
      };
    }

    case "pie":
    case "donut": {
      const s = spec.series[0] ?? { name: "", data: [] };
      return {
        ...base,
        legend: legendBase,
        series: [
          {
            type: "pie",
            name: s.name,
            radius: spec.chartType === "donut" ? ["48%", "74%"] : "72%",
            center: ["50%", "44%"],
            itemStyle: { borderColor: "#141413", borderWidth: 2 },
            label: { color: INK.secondary, fontSize: 11, formatter: (p: any) => clip(16)(String(p.name)) },
            labelLine: { lineStyle: { color: INK.axis }, length: 6, length2: 8 },
            labelLayout: { hideOverlap: true },
            data: cats.map((c, i) => ({ name: c, value: s.data[i] ?? 0 })),
          },
        ],
      };
    }

    case "scatter":
      return {
        ...base,
        xAxis: { type: "category", data: cats, name: spec.xAxis?.label, ...axis },
        yAxis: { type: "value", name: spec.yAxis?.label, ...axis },
        series: spec.series.slice(0, 3).map((s) => ({
          type: "scatter",
          name: s.name,
          data: s.data,
          symbolSize: 11,
          itemStyle: { opacity: 0.85 },
        })) as SeriesOption[],
      };

    case "radar":
      return {
        ...base,
        radar: {
          indicator: cats.map((c) => ({ name: c })),
          axisName: { color: INK.secondary, fontSize: 11 },
          splitLine: { lineStyle: { color: INK.grid } },
          axisLine: { lineStyle: { color: INK.axis } },
          splitArea: { show: false },
        },
        series: [
          {
            type: "radar",
            data: spec.series.map((s) => ({ name: s.name, value: s.data as number[] })),
            areaStyle: { opacity: 0.15 },
            lineStyle: { width: 2 },
          },
        ],
      };

    case "gauge": {
      const v = spec.series[0]?.data[0] ?? 0;
      return {
        ...base,
        legend: undefined,
        series: [
          {
            type: "gauge",
            startAngle: 200,
            endAngle: -20,
            min: 0,
            max: 100,
            progress: { show: true, width: 14, itemStyle: { color: SERIES[0] } },
            axisLine: { lineStyle: { width: 14, color: [[1, INK.grid]] } },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
            pointer: { show: false },
            detail: {
              valueAnimation: true,
              color: INK.primary,
              fontSize: 30,
              offsetCenter: [0, "10%"],
              formatter: `{value}${spec.yAxis?.unit ?? ""}`,
            },
            data: [{ value: v ?? 0 }],
          },
        ],
      };
    }

    case "sankey": {
      const links = spec.links ?? [];
      const names = [...new Set(links.flatMap((l) => [l.from, l.to]))];
      return {
        ...base,
        legend: undefined,
        series: [
          {
            type: "sankey",
            data: names.map((n) => ({ name: n })),
            links: links.map((l) => ({ source: l.from, target: l.to, value: l.value })),
            emphasis: { focus: "adjacency" },
            lineStyle: { color: "gradient", opacity: 0.35 },
            label: { color: INK.secondary, fontSize: 11 },
            itemStyle: { borderWidth: 0 },
          },
        ],
      };
    }

    case "heatmap": {
      const data: [number, number, number][] = [];
      spec.series.forEach((row, y) => row.data.forEach((v, x) => data.push([x, y, v ?? 0])));
      const vals = data.map((d) => d[2]);
      return {
        ...base,
        legend: undefined,
        grid: { left: 8, right: 16, top: 8, bottom: 48, containLabel: true },
        xAxis: { type: "category", data: cats, splitArea: { show: false }, ...axis },
        yAxis: { type: "category", data: spec.series.map((s) => s.name), splitArea: { show: false }, ...axis },
        visualMap: {
          min: Math.min(...vals, 0),
          max: Math.max(...vals, 1),
          orient: "horizontal",
          left: "center",
          bottom: 0,
          itemWidth: 10,
          itemHeight: 60,
          inRange: { color: SEQUENTIAL },
          textStyle: { color: INK.muted, fontSize: 10 },
        },
        series: [
          {
            type: "heatmap",
            data,
            itemStyle: { borderColor: "#141413", borderWidth: 2, borderRadius: 2 },
            label: { show: data.length <= 60, color: INK.primary, fontSize: 10 },
          },
        ],
      };
    }
  }
}

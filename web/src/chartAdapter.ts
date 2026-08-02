import type { EChartsOption } from "echarts";
import type { ChartTheme } from "./theme";

export type ChartType =
  | "line" | "area" | "stacked_area" | "step_line" | "bar" | "horizontal_bar"
  | "stacked_bar" | "stacked_horizontal_bar" | "pictorial_bar" | "scatter"
  | "effect_scatter" | "bubble" | "waterfall" | "theme_river"
  | "pie" | "donut" | "rose" | "funnel" | "gauge"
  | "treemap" | "sunburst" | "tree"
  | "sankey" | "graph" | "chord"
  | "boxplot" | "candlestick" | "heatmap" | "calendar" | "radar" | "parallel";

export interface ChartSpec {
  chartType: ChartType;
  xAxis?: { label?: string; categories: string[] };
  yAxis?: { label?: string; unit?: string };
  series: { name: string; data: (number | null)[] }[];
  links?: { from: string; to: string; value: number }[];
  hierarchy?: { name: string; parent?: string; value?: number }[];
  ohlc?: { date: string; open: number; high: number; low: number; close: number }[];
  boxes?: { name: string; min: number; q1: number; median: number; q3: number; max: number }[];
  calendar?: { date: string; value: number }[];
  zoom?: boolean;
}

const FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif";
const LABEL = 11;
const MICRO = 10;

const clip = (n: number) => (s: string) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function abbrev(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e12) return `${+(n / 1e12).toFixed(1)}T`;
  if (a >= 1e9) return `${+(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${+(n / 1e3).toFixed(1)}K`;
  return `${+n.toFixed(2)}`;
}

const ITEM_TRIGGER = new Set<ChartType>([
  "pie", "donut", "rose", "funnel", "gauge", "scatter", "effect_scatter", "bubble",
  "heatmap", "calendar", "sankey", "graph", "chord", "treemap", "sunburst", "tree",
  "parallel", "theme_river",
]);

const NO_LEGEND = new Set<ChartType>([
  "waterfall", "heatmap", "calendar", "gauge", "treemap", "sunburst", "tree",
  "sankey", "graph", "chord", "funnel", "boxplot", "candlestick",
]);

/** Flat parent-child list → nested tree. Flat is far easier for a model to emit correctly. */
function toTree(items: NonNullable<ChartSpec["hierarchy"]>) {
  type Node = { name: string; value?: number; children: Node[] };
  const nodes = new Map<string, Node>();
  for (const i of items) nodes.set(i.name, { name: i.name, value: i.value, children: [] });
  const roots: Node[] = [];
  for (const i of items) {
    const node = nodes.get(i.name)!;
    const parent = i.parent ? nodes.get(i.parent) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  const prune = (n: Node): any =>
    n.children.length ? { name: n.name, children: n.children.map(prune) } : { name: n.name, value: n.value ?? 0 };
  return roots.map(prune);
}

/** Vertical wash used for bar and area fills when the palette opts into gradients. */
function wash(hex: string, mode: "light" | "dark") {
  return {
    type: "linear" as const,
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: hex },
      { offset: 1, color: `${hex}${mode === "dark" ? "3d" : "33"}` },
    ],
  };
}

/** Typed spec in, renderer options out. The model never supplies ECharts config. */
export function specToOption(spec: ChartSpec, theme: ChartTheme, animate = true): EChartsOption {
  const t = spec.chartType;
  const SERIES = theme.series;
  const SEQUENTIAL = theme.sequential;
  const INK = theme.ink;
  const fill = (i: number) =>
    theme.gradient ? (wash(SERIES[i % SERIES.length], theme.mode) as any) : SERIES[i % SERIES.length];

  const catAxis = {
    type: "category" as const,
    axisTick: { show: false },
    axisLine: { lineStyle: { color: INK.axis } },
    axisLabel: { color: INK.muted, fontSize: LABEL, fontFamily: FONT, hideOverlap: true },
    splitLine: { show: false },
    nameTextStyle: { color: INK.muted, fontSize: MICRO },
  };

  const valAxis = {
    type: "value" as const,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: INK.muted,
      fontSize: LABEL,
      fontFamily: FONT,
      formatter: (v: number) => abbrev(v),
    },
    splitLine: { lineStyle: { color: INK.grid, width: 1 } },
    nameTextStyle: { color: INK.muted, fontSize: MICRO },
  };

  const legendBase = {
    type: "scroll" as const,
    bottom: 0,
    icon: "roundRect",
    itemWidth: 8,
    itemHeight: 8,
    itemGap: 14,
    formatter: clip(22),
    textStyle: { color: INK.secondary, fontSize: LABEL, fontFamily: FONT },
    pageTextStyle: { color: INK.muted, fontSize: MICRO },
    pageIconColor: INK.secondary,
    pageIconInactiveColor: INK.axis,
    pageIconSize: 9,
  };
  const cats = spec.xAxis?.categories ?? [];
  const series = spec.series ?? [];
  const unit = spec.yAxis?.unit ? ` ${spec.yAxis.unit}` : "";
  const showLegend = series.length > 1 && !NO_LEGEND.has(t);
  const bottomGap = showLegend ? 34 : 6;

  const base: EChartsOption = {
    color: SERIES,
    backgroundColor: "transparent",
    animation: animate,
    animationDuration: 700,
    animationDelay: (i: number) => i * 28,
    animationEasing: "cubicOut",
    textStyle: { color: INK.secondary, fontFamily: FONT, fontSize: LABEL },
    tooltip: {
      trigger: ITEM_TRIGGER.has(t) ? "item" : "axis",
      backgroundColor: theme.mode === "dark" ? "rgba(18,18,17,0.96)" : "rgba(255,255,255,0.97)",
      borderColor: INK.border,
      borderWidth: 1,
      padding: [9, 12],
      textStyle: { color: INK.primary, fontSize: 12, fontFamily: FONT },
      axisPointer: { type: "line", lineStyle: { color: INK.axis, width: 1 } },
      valueFormatter: (v: unknown) =>
        typeof v === "number" ? `${v.toLocaleString()}${unit}` : String(v ?? ""),
    },
    legend: showLegend ? legendBase : undefined,
    grid: { left: 6, right: 14, top: 10, bottom: bottomGap + (spec.zoom ? 30 : 0), containLabel: true },
    dataZoom: spec.zoom
      ? [
          { type: "inside" },
          {
            type: "slider",
            height: 18,
            bottom: showLegend ? 28 : 4,
            borderColor: "transparent",
            backgroundColor: INK.grid,
            fillerColor: theme.mode === "dark" ? "rgba(57,135,229,0.22)" : "rgba(42,120,214,0.16)",
            handleStyle: { color: SERIES[0], borderColor: SERIES[0] },
            moveHandleStyle: { color: SERIES[0] },
            dataBackground: { lineStyle: { color: INK.axis }, areaStyle: { color: "transparent" } },
            selectedDataBackground: { lineStyle: { color: SERIES[0] }, areaStyle: { color: "transparent" } },
            textStyle: { color: INK.muted, fontSize: MICRO },
          },
        ]
      : undefined,
  };

  const cartesian = (x: object, y: object) => ({
    ...base,
    xAxis: { ...x, name: undefined },
    yAxis: y,
  });

  switch (t) {
    case "line":
    case "area":
    case "stacked_area":
    case "step_line":
      return {
        ...cartesian(
          { ...catAxis, data: cats, boundaryGap: false },
          { ...valAxis, name: spec.yAxis?.label },
        ),
        series: series.map((s, i) => ({
          type: "line",
          name: s.name,
          data: s.data,
          step: t === "step_line" ? "middle" : undefined,
          stack: t === "stacked_area" ? "total" : undefined,
          symbolSize: 6,
          showSymbol: s.data.length <= 20,
          lineStyle: { width: 2 },
          emphasis: { focus: "series" },
          areaStyle:
            t === "area" || t === "stacked_area"
              ? { opacity: t === "stacked_area" ? 0.75 : 0.22, color: fill(i) }
              : undefined,
        })) as any,
      };

    case "bar":
    case "stacked_bar":
      return {
        ...cartesian({ ...catAxis, data: cats }, { ...valAxis, name: spec.yAxis?.label }),
        series: series.map((s, i) => ({
          type: "bar",
          name: s.name,
          data: s.data,
          stack: t === "stacked_bar" ? "total" : undefined,
          itemStyle: { color: fill(i), borderRadius: t === "stacked_bar" ? 2 : [4, 4, 0, 0] },
          barMaxWidth: 44,
          emphasis: { focus: "series" },
        })) as any,
      };

    case "horizontal_bar":
    case "stacked_horizontal_bar":
      return {
        ...base,
        grid: { left: 6, right: 18, top: 10, bottom: bottomGap + 12, containLabel: true },
        xAxis: { ...valAxis, name: spec.yAxis?.label, nameLocation: "middle", nameGap: 28 },
        yAxis: { ...catAxis, data: [...cats].reverse() },
        series: series.map((s, i) => ({
          type: "bar",
          name: s.name,
          data: [...s.data].reverse(),
          stack: t === "stacked_horizontal_bar" ? "total" : undefined,
          itemStyle: { color: fill(i), borderRadius: t === "stacked_horizontal_bar" ? 2 : [0, 4, 4, 0] },
          barMaxWidth: 26,
          emphasis: { focus: "series" },
        })) as any,
      };

    case "pictorial_bar":
      return {
        ...cartesian({ ...catAxis, data: cats }, { ...valAxis, name: spec.yAxis?.label }),
        series: series.map((s) => ({
          type: "pictorialBar",
          name: s.name,
          data: s.data,
          symbol: "roundRect",
          symbolRepeat: true,
          symbolSize: ["70%", 5],
          symbolMargin: 3,
          symbolClip: false,
        })) as any,
      };

    case "waterfall": {
      const values = (series[0]?.data ?? []).map((v) => v ?? 0);
      const support: number[] = [];
      let run = 0;
      for (const v of values) {
        support.push(v >= 0 ? run : run + v);
        run += v;
      }
      return {
        ...cartesian({ ...catAxis, data: cats }, { ...valAxis, name: spec.yAxis?.label }),
        series: [
          { type: "bar", stack: "wf", itemStyle: { color: "transparent" }, data: support, silent: true },
          {
            type: "bar",
            stack: "wf",
            barMaxWidth: 44,
            data: values.map((v) => ({
              value: Math.abs(v),
              itemStyle: { color: v >= 0 ? SERIES[2] : SERIES[7], borderRadius: 3 },
            })),
            label: {
              show: values.length <= 10,
              position: "top",
              color: INK.secondary,
              fontSize: MICRO,
              formatter: (p: any) => abbrev(values[p.dataIndex]),
            },
          },
        ] as any,
      };
    }

    case "scatter":
    case "effect_scatter":
    case "bubble": {
      const sizes = t === "bubble" ? (series[1]?.data ?? []) : [];
      const maxSize = Math.max(1, ...sizes.map((v) => Math.abs(v ?? 0)));
      return {
        ...cartesian(
          { ...catAxis, data: cats, name: spec.xAxis?.label },
          { ...valAxis, name: spec.yAxis?.label },
        ),
        series: (t === "bubble" ? series.slice(0, 1) : series.slice(0, 4)).map((s) => ({
          type: t === "effect_scatter" ? "effectScatter" : "scatter",
          name: s.name,
          data: s.data.map((v, i) => [i, v]),
          rippleEffect: t === "effect_scatter" ? { scale: 2.4 } : undefined,
          symbolSize:
            t === "bubble"
              ? (d: number[]) => 8 + (Math.abs(sizes[d[0]] ?? 0) / maxSize) * 34
              : 10,
          itemStyle: { opacity: 0.85 },
        })) as any,
      };
    }

    case "theme_river": {
      // themeRiver needs a numeric/time axis to lay out its bands, so index the categories.
      const data: [number, number, string][] = [];
      series.forEach((s) => s.data.forEach((v, i) => data.push([i, v ?? 0, s.name])));
      return {
        ...base,
        singleAxis: {
          type: "value",
          min: 0,
          max: Math.max(1, cats.length - 1),
          interval: 1,
          left: 10,
          right: 16,
          top: 12,
          bottom: bottomGap + 18,
          axisTick: { show: false },
          axisLine: { lineStyle: { color: INK.axis } },
          splitLine: { show: false },
          axisLabel: {
            color: INK.muted,
            fontSize: LABEL,
            fontFamily: FONT,
            formatter: (v: number) => cats[v] ?? "",
          },
        },
        series: [
          {
            type: "themeRiver",
            data,
            label: { show: false },
            emphasis: { focus: "series" },
            itemStyle: { shadowBlur: 0 },
          },
        ] as any,
      };
    }

    case "pie":
    case "donut":
    case "rose": {
      const s = series[0] ?? { name: "", data: [] };
      const total = s.data.reduce<number>((a, b) => a + (b ?? 0), 0) || 1;
      return {
        ...base,
        legend: { ...legendBase, show: cats.length <= 8 },
        series: [
          {
            type: "pie",
            name: s.name,
            roseType: t === "rose" ? "area" : undefined,
            radius: t === "donut" ? ["46%", "72%"] : t === "rose" ? ["18%", "74%"] : "70%",
            center: ["50%", "44%"],
            itemStyle: { borderColor: INK.surface, borderWidth: 2, borderRadius: 3 },
            label: {
              color: INK.secondary,
              fontSize: MICRO,
              formatter: (p: any) => `${clip(14)(String(p.name))}  ${((p.value / total) * 100).toFixed(0)}%`,
            },
            labelLine: { lineStyle: { color: INK.axis }, length: 6, length2: 8 },
            labelLayout: { hideOverlap: true },
            emphasis: { scaleSize: 6 },
            data: cats.map((c, i) => ({ name: c, value: s.data[i] ?? 0 })),
          },
        ] as any,
      };
    }

    case "funnel": {
      const s = series[0] ?? { name: "", data: [] };
      return {
        ...base,
        series: [
          {
            type: "funnel",
            name: s.name,
            left: "8%",
            right: "8%",
            top: 8,
            bottom: 8,
            gap: 2,
            minSize: "18%",
            label: { color: INK.primary, fontSize: LABEL, formatter: (p: any) => clip(20)(String(p.name)) },
            itemStyle: { borderColor: INK.surface, borderWidth: 2 },
            data: cats.map((c, i) => ({ name: c, value: s.data[i] ?? 0 })),
          },
        ] as any,
      };
    }

    case "gauge": {
      const v = series[0]?.data[0] ?? 0;
      return {
        ...base,
        series: [
          {
            type: "gauge",
            startAngle: 205,
            endAngle: -25,
            min: 0,
            max: 100,
            radius: "76%",
            center: ["50%", "60%"],
            progress: { show: true, width: 11, roundCap: true, itemStyle: { color: SERIES[0] } },
            axisLine: { roundCap: true, lineStyle: { width: 11, color: [[1, INK.grid]] } },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
            pointer: { show: false },
            title: { show: false },
            detail: {
              valueAnimation: true,
              color: INK.primary,
              fontSize: 26,
              fontWeight: 600,
              fontFamily: FONT,
              offsetCenter: [0, "26%"],
              formatter: `{value}${spec.yAxis?.unit ?? ""}`,
            },
            data: [{ value: +(v ?? 0).toFixed(1) }],
          },
        ] as any,
      };
    }

    case "treemap":
      return {
        ...base,
        series: [
          {
            type: "treemap",
            data: toTree(spec.hierarchy ?? []),
            roam: false,
            nodeClick: false,
            breadcrumb: { show: false },
            label: { color: "#fff", fontSize: LABEL, fontFamily: FONT, formatter: (p: any) => clip(18)(String(p.name)) },
            upperLabel: { show: true, height: 20, color: INK.secondary, fontSize: MICRO },
            itemStyle: { borderColor: INK.surface, borderWidth: 2, gapWidth: 2, borderRadius: 3 },
            levels: [{}, { itemStyle: { borderWidth: 2, gapWidth: 2 } }],
          },
        ] as any,
      };

    case "sunburst":
      return {
        ...base,
        series: [
          {
            type: "sunburst",
            data: toTree(spec.hierarchy ?? []),
            radius: ["16%", "92%"],
            center: ["50%", "50%"],
            nodeClick: false,
            itemStyle: { borderColor: INK.surface, borderWidth: 2 },
            label: { color: "#fff", fontSize: MICRO, minAngle: 14, formatter: (p: any) => clip(12)(String(p.name)) },
          },
        ] as any,
      };

    case "tree":
      return {
        ...base,
        series: [
          {
            type: "tree",
            data: toTree(spec.hierarchy ?? []),
            left: 62,
            right: 78,
            top: 14,
            bottom: 14,
            symbolSize: 8,
            orient: "LR",
            expandAndCollapse: false,
            itemStyle: { color: SERIES[0], borderWidth: 0 },
            lineStyle: { color: INK.axis, width: 1, curveness: 0.4 },
            label: { color: INK.secondary, fontSize: MICRO, position: "left", align: "right", distance: 6 },
            leaves: { label: { position: "right", align: "left" } },
          },
        ] as any,
      };

    case "sankey":
    case "chord":
    case "graph": {
      const links = spec.links ?? [];
      const names = [...new Set(links.flatMap((l) => [l.from, l.to]))];
      if (t === "sankey") {
        return {
          ...base,
          series: [
            {
              type: "sankey",
              data: names.map((n) => ({ name: n })),
              links: links.map((l) => ({ source: l.from, target: l.to, value: l.value })),
              left: 6,
              right: 78,
              top: 10,
              bottom: 10,
              emphasis: { focus: "adjacency" },
              lineStyle: { color: "gradient", opacity: 0.32 },
              label: { color: INK.secondary, fontSize: MICRO, formatter: (p: any) => clip(16)(String(p.name)) },
              itemStyle: { borderWidth: 0 },
            },
          ] as any,
        };
      }
      const weight = new Map<string, number>();
      for (const l of links) {
        weight.set(l.from, (weight.get(l.from) ?? 0) + l.value);
        weight.set(l.to, (weight.get(l.to) ?? 0) + l.value);
      }
      const maxW = Math.max(1, ...weight.values());
      return {
        ...base,
        series: [
          {
            type: "graph",
            layout: "circular",
            circular: { rotateLabel: false },
            left: 54,
            right: 54,
            top: 20,
            bottom: 20,
            data: names.map((n, i) => ({
              name: n,
              value: weight.get(n) ?? 0,
              symbolSize: 9 + ((weight.get(n) ?? 0) / maxW) * 22,
              itemStyle: { color: SERIES[i % SERIES.length] },
            })),
            links: links.map((l) => ({ source: l.from, target: l.to, value: l.value })),
            roam: false,
            label: {
              show: true,
              position: "right",
              color: INK.secondary,
              fontSize: MICRO,
              fontFamily: FONT,
              formatter: (p: any) => clip(12)(String(p.name)),
            },
            lineStyle: { color: "source", opacity: 0.28, curveness: 0.3 },
            emphasis: { focus: "adjacency", lineStyle: { opacity: 0.7, width: 2 } },
          },
        ] as any,
      };
    }

    case "boxplot": {
      const boxes = spec.boxes ?? [];
      return {
        ...cartesian(
          { ...catAxis, data: boxes.map((b) => b.name) },
          { ...valAxis, name: spec.yAxis?.label },
        ),
        series: [
          {
            type: "boxplot",
            data: boxes.map((b) => [b.min, b.q1, b.median, b.q3, b.max]),
            itemStyle: { color: "rgba(57,135,229,0.22)", borderColor: SERIES[0], borderWidth: 1.5 },
            boxWidth: [10, 42],
          },
        ] as any,
      };
    }

    case "candlestick": {
      const rows = spec.ohlc ?? [];
      return {
        ...cartesian(
          { ...catAxis, data: rows.map((r) => r.date) },
          { ...valAxis, name: spec.yAxis?.label, scale: true },
        ),
        series: [
          {
            type: "candlestick",
            data: rows.map((r) => [r.open, r.close, r.low, r.high]),
            itemStyle: {
              color: SERIES[2],
              color0: SERIES[7],
              borderColor: SERIES[2],
              borderColor0: SERIES[7],
              borderWidth: 1,
            },
          },
        ] as any,
      };
    }

    case "calendar": {
      const days = spec.calendar ?? [];
      const vals = days.map((d) => d.value);
      const year = days[0]?.date?.slice(0, 4) ?? String(new Date().getFullYear());
      return {
        ...base,
        grid: undefined,
        visualMap: {
          min: Math.min(0, ...vals),
          max: Math.max(1, ...vals),
          orient: "horizontal",
          left: "center",
          bottom: 2,
          itemWidth: 9,
          itemHeight: 50,
          inRange: { color: SEQUENTIAL },
          textStyle: { color: INK.muted, fontSize: MICRO },
        },
        calendar: {
          top: 24,
          left: 34,
          right: 12,
          cellSize: ["auto", 13],
          range: year,
          itemStyle: { color: "transparent", borderColor: INK.surface, borderWidth: 2 },
          splitLine: { show: false },
          yearLabel: { show: false },
          dayLabel: { color: INK.muted, fontSize: MICRO, nameMap: "en" },
          monthLabel: { color: INK.muted, fontSize: MICRO, nameMap: "en" },
        },
        series: [
          {
            type: "heatmap",
            coordinateSystem: "calendar",
            data: days.map((d) => [d.date, d.value]),
          },
        ] as any,
      };
    }

    case "radar":
      return {
        ...base,
        radar: {
          indicator: cats.map((c) => ({ name: clip(14)(c) })),
          center: ["50%", "48%"],
          radius: "66%",
          axisName: { color: INK.muted, fontSize: MICRO },
          splitLine: { lineStyle: { color: INK.grid } },
          axisLine: { lineStyle: { color: INK.grid } },
          splitArea: { show: false },
        },
        series: [
          {
            type: "radar",
            data: series.map((s) => ({ name: s.name, value: s.data as number[] })),
            areaStyle: { opacity: 0.14 },
            lineStyle: { width: 2 },
            symbolSize: 4,
          },
        ] as any,
      };

    case "parallel":
      // Each series is its own ECharts series (for legend + colour), so axis
      // extents must be set explicitly or they scale to the first series alone.
      return {
        ...base,
        parallelAxis: cats.map((c, i) => {
          const col = series.map((s) => s.data[i] ?? 0);
          const lo = Math.min(...col, 0);
          const hi = Math.max(...col, 1);
          return {
            dim: i,
            name: clip(11)(c),
            min: lo,
            max: hi,
            nameLocation: "end" as const,
            nameGap: 12,
            nameTextStyle: { color: INK.secondary, fontSize: MICRO, fontFamily: FONT },
            axisLine: { lineStyle: { color: INK.axis } },
            axisTick: { show: false },
            axisLabel: { color: INK.muted, fontSize: MICRO, formatter: (v: number) => abbrev(v) },
          };
        }),
        parallel: { left: 38, right: 38, top: 34, bottom: bottomGap + 12 },
        series: series.map((s) => ({
          type: "parallel",
          name: s.name,
          data: [s.data],
          smooth: true,
          lineStyle: { width: 2, opacity: 0.75 },
          emphasis: { lineStyle: { width: 3, opacity: 1 } },
        })) as any,
      };

    case "heatmap": {
      const data: [number, number, number][] = [];
      series.forEach((row, y) => row.data.forEach((v, x) => data.push([x, y, v ?? 0])));
      const vals = data.map((d) => d[2]);
      return {
        ...base,
        grid: { left: 6, right: 14, top: 10, bottom: 50, containLabel: true },
        xAxis: { ...catAxis, data: cats, splitArea: { show: false } },
        yAxis: { ...catAxis, data: series.map((s) => clip(16)(s.name)), splitArea: { show: false } },
        visualMap: {
          min: Math.min(...vals, 0),
          max: Math.max(...vals, 1),
          orient: "horizontal",
          left: "center",
          bottom: 2,
          itemWidth: 9,
          itemHeight: 54,
          inRange: { color: SEQUENTIAL },
          textStyle: { color: INK.muted, fontSize: MICRO },
        },
        series: [
          {
            type: "heatmap",
            data,
            itemStyle: { borderColor: INK.surface, borderWidth: 2, borderRadius: 2 },
            label: {
              show: data.length <= 48,
              color: INK.primary,
              fontSize: MICRO,
              formatter: (p: any) => abbrev(p.value[2]),
            },
          },
        ] as any,
      };
    }
  }
}

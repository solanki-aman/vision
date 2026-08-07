import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { specToOption, type ChartSpec } from "../chartAdapter";
import { STATUS } from "../theme";
import { useTheme } from "../ThemeContext";
import type { Binding, Fact, KpiSpec, TableSpec, NarrativeSpec, ImageSpec, LabelSpec, StatementSpec, HeroSpec, Widget } from "../types";
import { useFilters, applyWindow } from "../FilterContext";
import { matchEntity } from "../entities";
import { ProvBadge } from "../Provenance";

/** The fact bound at a given spec path on this widget, if any. */
function factAt(bindings: Binding[] | null | undefined, facts: Record<string, Fact>, path: string): Fact | undefined {
  const b = (bindings ?? []).find((x) => x.path === path);
  return b ? facts[b.factId] : undefined;
}

/** ECharts paints into a <canvas>, which is nothing at all to a screen reader.
 * Describe the chart in words, and put the numbers in a real table beside it so
 * the data itself is reachable — not just a label saying a chart exists. */
function chartSummary(spec: ChartSpec, title: string): string {
  const kind = (spec.chartType ?? "chart").replace(/_/g, " ");
  const cats = spec.xAxis?.categories ?? [];
  const series = spec.series ?? [];
  const parts = [`${kind} chart.`, title];
  if (cats.length) {
    parts.push(`${cats.length} categories from ${cats[0]} to ${cats[cats.length - 1]}.`);
  }
  if (series.length) {
    parts.push(`${series.length} series: ${series.map((s) => s.name).join(", ")}.`);
  }
  if (spec.yAxis?.unit) parts.push(`Values in ${spec.yAxis.unit}.`);
  return parts.filter(Boolean).join(" ");
}

/** The chart's numbers as a table, visually hidden but available to assistive tech. */
function ChartTable({ spec, title }: { spec: ChartSpec; title: string }) {
  const cats = spec.xAxis?.categories ?? [];
  const series = (spec.series ?? []).filter((s) => (s.data ?? []).length);
  if (!cats.length || !series.length) return null;
  return (
    <table className="sr-only">
      <caption>{title} — data table</caption>
      <thead>
        <tr>
          <th scope="col">{spec.xAxis?.label ?? "Category"}</th>
          {series.map((s) => (
            <th key={s.name} scope="col">
              {s.name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {cats.map((c, i) => (
          <tr key={c}>
            <th scope="row">{c}</th>
            {series.map((s) => (
              <td key={s.name}>{s.data?.[i] ?? "no data"}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Chart({ spec, widgetId, entities, title = "" }: { spec: ChartSpec; widgetId: string; entities: Record<string, string>; title?: string }) {
  const { chart: theme, animate } = useTheme();
  const { windows } = useFilters();
  const view = applyWindow(spec, windows[widgetId]);
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      c.dispose();
    };
  }, []);

  useEffect(() => {
    if (!chart.current) return;
    try {
      chart.current.setOption(specToOption(view, theme, animate, entities), true);
    } catch (e) {
      console.error("chart render failed", e, view);
    }
  }, [view, theme, animate, entities]);

  // Presentation mode re-runs the entrance animation when a row is revealed.
  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const replay = () => {
      const c = chart.current;
      if (!c) return;
      c.clear();
      try {
        c.setOption(specToOption(view, theme, true, entities));
      } catch {
        /* keep presenting */
      }
    };
    node.addEventListener("vision:replay", replay);
    return () => node.removeEventListener("vision:replay", replay);
  }, [view, theme, entities]);

  return (
    <>
      <div className="chart-mount" ref={el} role="img" aria-label={chartSummary(view, title)} />
      <ChartTable spec={view} title={title} />
    </>
  );
}

function fmt(n: number) {
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e4) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function Sparkline({ points, color }: { points: number[]; color?: string }) {
  const { chart: theme } = useTheme();
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const d = points
    .map((p, i) => `${(i / (points.length - 1)) * 100},${28 - ((p - min) / span) * 26}`)
    .join(" ");
  return (
    <svg className="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden>
      <polyline points={d} fill="none" stroke={color ?? theme.series[0]} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Kpi({ spec, accent, fact, facts }: { spec: KpiSpec; accent?: string; fact?: Fact; facts?: Record<string, Fact> }) {
  const c = spec.comparison;
  const delta = c ? spec.value - c.baseline : 0;
  const pct = c && c.baseline !== 0 ? (delta / Math.abs(c.baseline)) * 100 : null;
  const good =
    !c || c.favorableDirection === "neutral" ? null : delta > 0 === (c.favorableDirection === "up");
  // A wild percentage means the model sent a mismatched baseline — show the raw delta instead.
  const readable = pct !== null && Math.abs(pct) < 1000;

  return (
    <div className="kpi">
      <div className="kpi-value">
        <span>{fmt(spec.value)}</span>
        {spec.unit && <span className="kpi-unit">{spec.unit}</span>}
        {fact && facts && (
          <ProvBadge facts={[fact]} all={facts} headline={`${fmt(spec.value)}${spec.unit ? ` ${spec.unit}` : ""}`} />
        )}
      </div>
      <div className="kpi-label" title={spec.label}>
        {accent && <span className="edot" style={{ background: accent }} aria-hidden />}
        {spec.label}
      </div>
      {c && (
        <div className="kpi-delta" style={{ color: good === null ? undefined : good ? STATUS.good : STATUS.critical }}>
          <span aria-hidden>{delta >= 0 ? "▲" : "▼"}</span>
          {readable ? `${Math.abs(pct!).toFixed(1)}%` : fmt(Math.abs(delta))}{" "}
          <span className="kpi-vs">vs {c.label}</span>
        </div>
      )}
      {spec.sparkline && spec.sparkline.length > 1 && <Sparkline points={spec.sparkline} color={accent} />}
    </div>
  );
}

function Table({ spec, entities }: { spec: TableSpec; entities: Record<string, string> }) {
  const firstKey = spec.columns[0]?.key;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {spec.columns.map((c) => (
              <th key={c.key} style={{ textAlign: c.align ?? "left" }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spec.rows.map((r, i) => (
            <tr key={i}>
              {spec.columns.map((c) => (
                <td key={c.key} style={{ textAlign: c.align ?? "left" }}>
                  {c.key === firstKey && typeof r[c.key] === "string" && matchEntity(String(r[c.key]), entities) && (
                    <span className="edot" style={{ background: matchEntity(String(r[c.key]), entities) }} aria-hidden />
                  )}
                  {typeof r[c.key] === "number" ? fmt(r[c.key] as number) : (r[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Narrative({ spec }: { spec: NarrativeSpec }) {
  return (
    <div className={`narrative tone-${spec.tone ?? "neutral"}`}>
      <p>{spec.body}</p>
      {spec.bullets && spec.bullets.length > 0 && (
        <ul>
          {spec.bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Img({ spec }: { spec: ImageSpec }) {
  return <img className="gen-image" src={spec.url} alt={spec.prompt} loading="lazy" />;
}

function Label({ spec }: { spec: LabelSpec }) {
  return (
    <div className="labelband">
      <span className="labelband-text">{spec.text}</span>
      {spec.note && <span className="labelband-note">{spec.note}</span>}
    </div>
  );
}

function Statement({ spec }: { spec: StatementSpec }) {
  const sign = { add: "+", subtract: "−", subtotal: "=", total: "=" } as const;
  return (
    <div className="statement">
      {spec.unit && <div className="statement-unit">{spec.unit}</div>}
      {spec.lines.map((l, i) => (
        <div key={i} className={`stmt-line role-${l.role} ${l.indent ? "indent" : ""}`}>
          <span className="stmt-sign" aria-hidden>
            {sign[l.role]}
          </span>
          <span className="stmt-label">{l.label}</span>
          {l.percent !== undefined && <span className="stmt-pct">{l.percent.toFixed(1)}%</span>}
          <span className="stmt-value">
            {l.role === "subtract" ? `(${fmt(Math.abs(l.value))})` : fmt(l.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function Hero({ spec }: { spec: HeroSpec }) {
  return (
    <div className="hero">
      {spec.kicker && <span className="hero-kicker">{spec.kicker}</span>}
      <span className="hero-display">{spec.display}</span>
      {spec.dek && <span className="hero-dek">{spec.dek}</span>}
    </div>
  );
}

export function WidgetBody({
  widget,
  entities,
  allWidgets,
  facts,
}: {
  widget: Widget;
  entities: Record<string, string>;
  allWidgets?: Widget[];
  facts?: Record<string, Fact>;
}) {
  switch (widget.kind) {
    case "chart":
      return (
        <Chart
          spec={widget.spec as ChartSpec}
          widgetId={widget.id}
          entities={entities}
          title={widget.title}
        />
      );
    case "label":
      return <Label spec={widget.spec as LabelSpec} />;
    case "statement":
      return <Statement spec={widget.spec as StatementSpec} />;
    case "hero":
      return <Hero spec={widget.spec as HeroSpec} />;
    case "kpi":
      return (
        <Kpi
          spec={widget.spec as KpiSpec}
          accent={matchEntity(`${widget.title} ${(widget.spec as KpiSpec).label ?? ""}`, entities)}
          fact={facts && factAt(widget.bindings, facts, "value")}
          facts={facts}
        />
      );
    case "table":
      return <Table spec={widget.spec as TableSpec} entities={entities} />;
    case "narrative":
      return <Narrative spec={widget.spec as NarrativeSpec} />;
    case "image":
      return <Img spec={widget.spec as ImageSpec} />;
  }
}

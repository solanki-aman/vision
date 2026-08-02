import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { specToOption, type ChartSpec } from "../chartAdapter";
import { STATUS } from "../theme";
import { useTheme } from "../ThemeContext";
import type { KpiSpec, TableSpec, NarrativeSpec, ImageSpec, ControlSpec, LabelSpec, StatementSpec, HeroSpec, Widget } from "../types";
import { useFilters, applyWindow } from "../FilterContext";

function Chart({ spec, widgetId }: { spec: ChartSpec; widgetId: string }) {
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
      chart.current.setOption(specToOption(view, theme, animate), true);
    } catch (e) {
      console.error("chart render failed", e, view);
    }
  }, [view, theme, animate]);

  // Presentation mode re-runs the entrance animation when a row is revealed.
  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const replay = () => {
      const c = chart.current;
      if (!c) return;
      c.clear();
      try {
        c.setOption(specToOption(view, theme, true));
      } catch {
        /* keep presenting */
      }
    };
    node.addEventListener("vision:replay", replay);
    return () => node.removeEventListener("vision:replay", replay);
  }, [view, theme]);

  return <div className="chart-mount" ref={el} />;
}

function fmt(n: number) {
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e4) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function Sparkline({ points }: { points: number[] }) {
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
      <polyline points={d} fill="none" stroke={theme.series[0]} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Kpi({ spec }: { spec: KpiSpec }) {
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
      </div>
      <div className="kpi-label" title={spec.label}>
        {spec.label}
      </div>
      {c && (
        <div className="kpi-delta" style={{ color: good === null ? undefined : good ? STATUS.good : STATUS.critical }}>
          <span aria-hidden>{delta >= 0 ? "▲" : "▼"}</span>
          {readable ? `${Math.abs(pct!).toFixed(1)}%` : fmt(Math.abs(delta))}{" "}
          <span className="kpi-vs">vs {c.label}</span>
        </div>
      )}
      {spec.sparkline && spec.sparkline.length > 1 && <Sparkline points={spec.sparkline} />}
    </div>
  );
}

function Table({ spec }: { spec: TableSpec }) {
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

function RangeControl({ spec }: { spec: ControlSpec }) {
  const { windows, setWindow } = useFilters();
  const current = windows[spec.targets[0]] ?? [0, 100];
  const [lo, hi] = current;

  const move = (which: 0 | 1) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = Number(e.target.value);
    const next: [number, number] = which === 0 ? [Math.min(v, hi - 5), hi] : [lo, Math.max(v, lo + 5)];
    setWindow(spec.targets, next);
  };

  return (
    <div className="range">
      <div className="range-head">
        <span className="range-label">{spec.label}</span>
        <span className="range-value">
          {Math.round(lo)}% – {Math.round(hi)}%
          <button className="range-reset" onClick={() => setWindow(spec.targets, [0, 100])}>
            reset
          </button>
        </span>
      </div>
      <div className="range-track">
        <span className="range-fill" style={{ left: `${lo}%`, right: `${100 - hi}%` }} />
        <input type="range" min={0} max={100} value={lo} onChange={move(0)} aria-label={`${spec.label} start`} />
        <input type="range" min={0} max={100} value={hi} onChange={move(1)} aria-label={`${spec.label} end`} />
      </div>
    </div>
  );
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

export function WidgetBody({ widget }: { widget: Widget }) {
  switch (widget.kind) {
    case "chart":
      return <Chart spec={widget.spec as ChartSpec} widgetId={widget.id} />;
    case "control":
      return <RangeControl spec={widget.spec as ControlSpec} />;
    case "label":
      return <Label spec={widget.spec as LabelSpec} />;
    case "statement":
      return <Statement spec={widget.spec as StatementSpec} />;
    case "hero":
      return <Hero spec={widget.spec as HeroSpec} />;
    case "kpi":
      return <Kpi spec={widget.spec as KpiSpec} />;
    case "table":
      return <Table spec={widget.spec as TableSpec} />;
    case "narrative":
      return <Narrative spec={widget.spec as NarrativeSpec} />;
    case "image":
      return <Img spec={widget.spec as ImageSpec} />;
  }
}

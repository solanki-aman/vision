import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { specToOption, type ChartSpec } from "../chartAdapter";

export function ChartCard({ spec }: { spec: ChartSpec }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    try {
      chartRef.current?.setOption(specToOption(spec), true);
    } catch (e) {
      console.error("failed to render spec", e, spec);
    }
  }, [spec]);

  return (
    <figure className="chart-card">
      <figcaption>
        <h3>{spec.title}</h3>
        {spec.takeaway && <p className="takeaway">{spec.takeaway}</p>}
      </figcaption>
      <div className="chart-canvas" ref={ref} role="img" aria-label={`${spec.title}. ${spec.takeaway}`} />
    </figure>
  );
}

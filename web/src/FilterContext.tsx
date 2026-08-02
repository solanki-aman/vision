import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

/** Percent window [start, end] over a chart's x-axis, keyed by widget id. */
export type Window = [number, number];

interface Ctx {
  windows: Record<string, Window>;
  setWindow: (widgetIds: string[], w: Window) => void;
}

const FilterCtx = createContext<Ctx>({ windows: {}, setWindow: () => {} });

export function FilterProvider({ children }: { children: ReactNode }) {
  const [windows, setWindows] = useState<Record<string, Window>>({});

  const setWindow = useCallback((widgetIds: string[], w: Window) => {
    setWindows((prev) => {
      const next = { ...prev };
      for (const id of widgetIds) next[id] = w;
      return next;
    });
  }, []);

  const value = useMemo(() => ({ windows, setWindow }), [windows, setWindow]);
  return <FilterCtx.Provider value={value}>{children}</FilterCtx.Provider>;
}

export const useFilters = () => useContext(FilterCtx);

/** Slice a chart's categories and series to a percent window. */
export function applyWindow<T extends { xAxis?: { categories: string[] }; series: { name: string; data: (number | null)[] }[] }>(
  spec: T,
  win?: Window,
): T {
  const cats = spec.xAxis?.categories;
  if (!win || !cats || cats.length < 2) return spec;
  const [lo, hi] = win;
  if (lo <= 0 && hi >= 100) return spec;

  const n = cats.length;
  const start = Math.max(0, Math.floor((lo / 100) * n));
  const end = Math.min(n, Math.max(start + 1, Math.ceil((hi / 100) * n)));

  return {
    ...spec,
    xAxis: { ...spec.xAxis, categories: cats.slice(start, end) },
    series: spec.series.map((s) => ({ ...s, data: s.data.slice(start, end) })),
  };
}

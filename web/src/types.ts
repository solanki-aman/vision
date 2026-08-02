import type { ChartSpec } from "./chartAdapter";

export interface Provenance {
  source: string;
  asOf?: string;
  confidence: "measured" | "estimated" | "illustrative";
  note?: string;
}

export interface KpiSpec {
  value: number;
  unit?: string;
  label: string;
  comparison?: { baseline: number; label: string; favorableDirection: "up" | "down" | "neutral" };
  sparkline?: number[];
}

export interface TableSpec {
  columns: { key: string; label: string; align?: "left" | "right" }[];
  rows: Record<string, string | number | null>[];
}

export interface NarrativeSpec {
  body: string;
  bullets?: string[];
  tone: "neutral" | "positive" | "caution" | "critical";
}

export interface ImageSpec {
  url: string;
  prompt: string;
}

export interface ControlSpec {
  control: "range";
  label: string;
  targets: string[];
}

export interface LabelSpec {
  text: string;
  note?: string;
}

export interface StatementSpec {
  unit?: string;
  lines: {
    label: string;
    value: number;
    role: "add" | "subtract" | "subtotal" | "total";
    percent?: number;
    indent?: boolean;
  }[];
}

export interface Widget {
  id: string;
  kind: "chart" | "kpi" | "table" | "narrative" | "image" | "control" | "label" | "statement";
  title: string;
  spec: ChartSpec | KpiSpec | TableSpec | NarrativeSpec | ImageSpec | ControlSpec | LabelSpec | StatementSpec;
  provenance: Provenance | null;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface CanvasState {
  canvas: { id: string; title: string; current_version: number } | null;
  widgets: Widget[];
}

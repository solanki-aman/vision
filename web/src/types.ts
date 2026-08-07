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

export interface HeroSpec {
  display: string;
  dek?: string;
  kicker?: string;
}

export interface Binding {
  path: string;
  factId: string;
}

/** A number (or series) with its lineage — how the canvas knows where a figure came from. */
export interface Fact {
  factId: string;
  kind: "scalar" | "series";
  entity?: string | null;
  label: string;
  unit?: string | null;
  asOf?: string | null;
  value?: number | null;
  points?: { x: string; y: number | null }[] | null;
  tool: string;
  query?: string | null;
  snippet?: string | null;
  sourceUrl?: string | null;
  confidence: "measured" | "estimated" | "illustrative";
  derivedFrom?: string[] | null;
  formula?: string | null;
  inputs?: { name: string; factId: string }[] | null;
}

export interface Widget {
  id: string;
  kind: "chart" | "kpi" | "table" | "narrative" | "image" | "label" | "statement" | "hero";
  title: string;
  spec: ChartSpec | KpiSpec | TableSpec | NarrativeSpec | ImageSpec | LabelSpec | StatementSpec | HeroSpec;
  provenance: Provenance | null;
  bindings?: Binding[] | null;
  /** Fact ids this widget's title/prose was written from — provenance for claims. */
  authoredFrom?: string[] | null;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface CanvasStyle {
  name: string;
  accent: string;
  type: "sans" | "serif" | "mono";
  paper: "default" | "cream" | "cool" | "sage" | "blush";
  cards: "bordered" | "flat" | "ghost";
  reason: string;
}

export interface CanvasState {
  canvas: { id: string; title: string; current_version: number; style?: CanvasStyle | null } | null;
  widgets: Widget[];
}

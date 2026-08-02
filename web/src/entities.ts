import type { Widget } from "./types";

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * Names that recur across widgets get a stable palette slot, so the same
 * ticker or team wears one colour in every chart on the canvas. One-off
 * categories stay neutral.
 */
export function buildEntityColors(widgets: Widget[], palette: string[]): Record<string, string> {
  const order: string[] = [];
  const seen = new Set<string>();

  const sorted = [...widgets].sort((a, b) => (a.y ?? 0) - (b.y ?? 0) || (a.x ?? 0) - (b.x ?? 0));
  for (const w of sorted) {
    if (w.kind !== "chart") continue;
    const s: any = w.spec;
    const names: string[] =
      (s.series?.length ?? 0) > 1
        ? s.series.map((x: any) => String(x.name))
        : ["bar", "horizontal_bar", "pie", "donut", "rose", "slope"].includes(s.chartType)
          ? (s.xAxis?.categories ?? []).map(String)
          : [];
    for (const n of new Set(names)) {
      if (!seen.has(n) && n.length >= 2 && n.length <= 24) {
        seen.add(n);
        order.push(n);
      }
    }
  }

  const blobs = widgets.map((w) => JSON.stringify({ t: w.title, s: w.spec }));
  const map: Record<string, string> = {};
  let slot = 0;
  for (const name of order) {
    const re = new RegExp(`\\b${esc(name)}\\b`, "i");
    const appearances = blobs.filter((b) => re.test(b)).length;
    if (appearances >= 2) map[name] = palette[slot++ % palette.length];
    if (slot >= palette.length) break;
  }
  return slot >= 2 ? map : {};
}

/** Find the entity whose name appears in a piece of text, if any. */
export function matchEntity(text: string, entities: Record<string, string>): string | undefined {
  for (const [name, color] of Object.entries(entities)) {
    if (new RegExp(`\\b${esc(name)}\\b`, "i").test(text)) return color;
  }
  return undefined;
}

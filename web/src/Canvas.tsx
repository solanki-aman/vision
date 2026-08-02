import { useEffect, useRef, useState } from "react";
import { GridStack, type GridStackNode } from "gridstack";
import "gridstack/dist/gridstack.min.css";
import { WidgetBody } from "./widgets/WidgetBody";
import type { Widget } from "./types";

const CONFIDENCE_LABEL = {
  measured: "measured",
  estimated: "estimated",
  illustrative: "illustrative",
} as const;

interface Props {
  widgets: Widget[];
  entities: Record<string, string>;
  /** Present mode: rows with y above this stay hidden. null = not presenting. */
  revealY: number | null;
  onLayoutChange: (ops: { kind: "move_widget" | "resize_widget"; widgetId: string; x?: number; y?: number; w?: number; h?: number }[]) => void;
  onRemove: (widgetId: string) => void;
}

const FULL_COLUMNS = 12;

function columnsFor(width: number) {
  if (width < 720) return 3;
  if (width < 1040) return 6;
  return FULL_COLUMNS;
}

export function Canvas({ widgets, entities, revealY, onLayoutChange, onRemove }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const grid = useRef<GridStack | null>(null);
  const items = useRef(new Map<string, HTMLDivElement>());
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const known = useRef(new Set<string>());
  // Programmatic updates also fire GridStack's change event; don't echo those back as commands.
  const quiet = useRef(0);

  const silently = (fn: () => void) => {
    quiet.current += 1;
    try {
      fn();
    } finally {
      setTimeout(() => {
        quiet.current -= 1;
      }, 60);
    }
  };

  useEffect(() => {
    if (!host.current) return;
    const g = GridStack.init(
      {
        column: FULL_COLUMNS,
        cellHeight: 40,
        margin: 8,
        float: false,
        animate: true,
        resizable: { handles: "se, sw" },
        handleClass: "widget-grip",
      },
      host.current,
    );
    if (!g) return;
    grid.current = g;

    g.on("change", (_e, nodes: GridStackNode[]) => {
      // Below full width the grid is a scaled view; persisting those coords would corrupt the layout.
      if (quiet.current > 0 || g.getColumn() !== FULL_COLUMNS) return;
      const ops = nodes.flatMap((n) => {
        const id = (n.el as HTMLElement | undefined)?.dataset.widgetId;
        if (!id) return [];
        return [
          { kind: "move_widget" as const, widgetId: id, x: n.x ?? 0, y: n.y ?? 0 },
          { kind: "resize_widget" as const, widgetId: id, w: n.w ?? 4, h: n.h ?? 4 },
        ];
      });
      if (ops.length) onLayoutChange(ops);
    });

    const ro = new ResizeObserver(([entry]) => {
      const next = columnsFor(entry.contentRect.width);
      if (next !== g.getColumn()) silently(() => g.column(next, "moveScale"));
    });
    ro.observe(host.current);

    return () => {
      ro.disconnect();
      g.destroy(false);
      grid.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const g = grid.current;
    if (!g) return;
    const arriving = new Set<string>();

    // Stored coordinates are in the canonical 12-column space; only honour them there.
    const canonical = g.getColumn() === FULL_COLUMNS;

    silently(() => {
      g.batchUpdate();
      for (const w of widgets) {
        const el = items.current.get(w.id);
        if (!el) continue;
        if (!(el as any).gridstackNode) {
          if (canonical) {
            el.setAttribute("gs-x", String(w.x ?? 0));
            el.setAttribute("gs-y", String(w.y ?? 0));
          }
          el.setAttribute("gs-w", String(canonical ? (w.w ?? 4) : g.getColumn()));
          el.setAttribute("gs-h", String(w.h ?? 4));
          g.makeWidget(el);
          if (!known.current.has(w.id)) arriving.add(w.id);
          known.current.add(w.id);
        } else if (canonical) {
          const n = (el as any).gridstackNode as GridStackNode;
          if (n.x !== w.x || n.y !== w.y || n.w !== w.w || n.h !== w.h) {
            g.update(el, { x: w.x, y: w.y, w: w.w, h: w.h });
          }
        }
      }
      g.batchUpdate(false);

      const live = new Set(widgets.map((w) => w.id));
      for (const [id, el] of items.current) {
        if (!live.has(id) && (el as any).gridstackNode) {
          g.removeWidget(el, false);
          items.current.delete(id);
          known.current.delete(id);
        }
      }
    });

    if (arriving.size) {
      setFresh(arriving);
      const t = setTimeout(() => setFresh(new Set()), 1400);
      return () => clearTimeout(t);
    }
  }, [widgets]);

  // Stable DOM order so React never reorders nodes GridStack is positioning.
  const ordered = [...widgets].sort((a, b) => a.id.localeCompare(b.id));

  return (
    <div className="grid-stack" ref={host}>
      {ordered.map((w) => (
        <div
          key={w.id}
          className="grid-stack-item"
          data-widget-id={w.id}
          ref={(el) => {
            if (el) items.current.set(w.id, el);
          }}
        >
          <div
            className={`grid-stack-item-content widget kind-${w.kind} ${fresh.has(w.id) ? "arriving" : ""} ${
              revealY !== null && (w.y ?? 0) > revealY ? "unrevealed" : ""
            }`}
          >
            {w.kind !== "label" && w.kind !== "hero" && (
            <header className="widget-grip">
              <h3 title={w.title}>{w.title}</h3>
              <button className="widget-x" onClick={() => onRemove(w.id)} aria-label={`Remove ${w.title}`}>
                ✕
              </button>
            </header>
            )}
            <div className="widget-body">
              <WidgetBody widget={w} entities={entities} />
            </div>
            {w.provenance && (
              <footer className={`prov prov-${w.provenance.confidence}`}>
                <span className="prov-dot" aria-hidden />
                <span className="prov-text">
                  {w.provenance.source}
                  {w.provenance.asOf ? ` · ${w.provenance.asOf}` : ""} ·{" "}
                  {CONFIDENCE_LABEL[w.provenance.confidence]}
                </span>
              </footer>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

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
  onLayoutChange: (ops: { kind: "move_widget" | "resize_widget"; widgetId: string; x?: number; y?: number; w?: number; h?: number }[]) => void;
  onRemove: (widgetId: string) => void;
}

export function Canvas({ widgets, onLayoutChange, onRemove }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const grid = useRef<GridStack | null>(null);
  const items = useRef(new Map<string, HTMLDivElement>());
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const known = useRef(new Set<string>());

  useEffect(() => {
    if (!host.current) return;
    const g = GridStack.init(
      {
        column: 12,
        cellHeight: 76,
        margin: 7,
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

    return () => {
      g.destroy(false);
      grid.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const g = grid.current;
    if (!g) return;
    const arriving = new Set<string>();

    g.batchUpdate();
    for (const w of widgets) {
      const el = items.current.get(w.id);
      if (!el) continue;
      if (!(el as any).gridstackNode) {
        el.setAttribute("gs-x", String(w.x ?? 0));
        el.setAttribute("gs-y", String(w.y ?? 0));
        el.setAttribute("gs-w", String(w.w ?? 4));
        el.setAttribute("gs-h", String(w.h ?? 4));
        g.makeWidget(el);
        if (!known.current.has(w.id)) arriving.add(w.id);
        known.current.add(w.id);
      } else {
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
          <div className={`grid-stack-item-content widget kind-${w.kind} ${fresh.has(w.id) ? "arriving" : ""}`}>
            <header className="widget-grip">
              <h3 title={w.title}>{w.title}</h3>
              <button className="widget-x" onClick={() => onRemove(w.id)} aria-label={`Remove ${w.title}`}>
                ✕
              </button>
            </header>
            <div className="widget-body">
              <WidgetBody widget={w} />
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

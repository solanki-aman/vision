import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import type { UIMessage } from "ai";
import { Canvas } from "./Canvas";
import { StreamRail } from "./StreamRail";
import { Settings } from "./Settings";
import { useTheme } from "./ThemeContext";
import { buildEntityColors } from "./entities";
import { FilterProvider } from "./FilterContext";
import type { CanvasState, CanvasStyle, Fact } from "./types";

interface CanvasListItem {
  id: string;
  title: string;
  widget_count: string;
  updated_at: string;
}

const PROMPTS = [
  "Where is the AI capex actually going?",
  "Show me the shape of the housing market right now",
  "What does a great week of sleep look like?",
  "Compare the last four SpaceX launch cadences",
];

function CanvasView({ canvasId, onTitle }: { canvasId: string; onTitle: (t: string) => void }) {
  const [state, setState] = useState<CanvasState>({ canvas: null, widgets: [] });
  const [facts, setFacts] = useState<Record<string, Fact>>({});
  const { chart: chartTheme, mode } = useTheme();
  const [railOpen, setRailOpen] = useState(true);
  const [input, setInput] = useState("");
  const [initial, setInitial] = useState<UIMessage[] | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const refetch = useCallback(async () => {
    const res = await fetch(`/api/canvases/${canvasId}`);
    const next: CanvasState = await res.json();
    setState(next);
    if (next.canvas) onTitle(next.canvas.title);
    fetch(`/api/canvases/${canvasId}/facts`)
      .then((r) => r.json())
      .then((rows: Fact[]) => {
        const map: Record<string, Fact> = {};
        for (const f of rows) map[f.factId] = f;
        setFacts(map);
      })
      .catch(() => {});
  }, [canvasId, onTitle]);

  useEffect(() => {
    refetch();
    fetch(`/api/canvases/${canvasId}/messages`)
      .then((r) => r.json())
      .then((rows) => setInitial(rows as UIMessage[]));
  }, [canvasId, refetch]);

  useEffect(() => {
    const es = new EventSource(`/api/canvases/${canvasId}/events`);
    es.onmessage = () => refetch();
    return () => es.close();
  }, [canvasId, refetch]);

  // useChat only reads `messages` on first render, so the surface is keyed on
  // the loaded history — otherwise a reload shows an empty stream.
  const { messages, sendMessage, status, error } = useChat({
    id: `${canvasId}:${initial ? "ready" : "loading"}`,
    messages: initial ?? [],
    transport: new DefaultChatTransport({ api: "/api/chat", body: { canvasId } }),
  });

  const busy = status === "submitted" || status === "streaming";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        box.current?.focus();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "z") {
        e.preventDefault();
        fetch(`/api/canvases/${canvasId}/undo`, { method: "POST" }).then(refetch);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canvasId, refetch]);

  const send = (text: string) => {
    if (!text.trim() || busy) return;
    sendMessage({ text });
    setInput("");
  };

  const applyOps = useCallback(
    (operations: any[]) => {
      fetch(`/api/canvases/${canvasId}/commands`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operations, origin: "direct_manipulation" }),
      });
    },
    [canvasId],
  );

  const empty = state.widgets.length === 0;
  const bare = new URLSearchParams(location.search).get("bare") === "1";

  // Presentation mode: the canvas plays row by row.
  const [present, setPresent] = useState(false);
  const [step, setStep] = useState(0);
  const rowYs = useMemo(
    () => [...new Set(state.widgets.map((w) => w.y ?? 0))].sort((a, b) => a - b),
    [state.widgets],
  );
  const revealY = present ? (rowYs[Math.min(step, rowYs.length - 1)] ?? 0) : null;

  const enterPresent = () => {
    setStep(0);
    setPresent(true);
  };
  const exitPresent = useCallback(() => setPresent(false), []);

  const goto = useCallback(
    (next: number) => {
      const clamped = Math.max(0, Math.min(rowYs.length - 1, next));
      setStep(clamped);
      const y = rowYs[clamped];
      const first = state.widgets
        .filter((w) => (w.y ?? 0) === y)
        .sort((a, b) => (a.x ?? 0) - (b.x ?? 0))[0];
      if (!first) return;
      const el = document.querySelector(`[data-widget-id="${first.id}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      // Let the fade-in start, then replay chart entrances for the revealed row.
      setTimeout(() => {
        for (const w of state.widgets.filter((w) => (w.y ?? 0) === y)) {
          document
            .querySelector(`[data-widget-id="${w.id}"] .chart-mount`)
            ?.dispatchEvent(new CustomEvent("vision:replay"));
        }
      }, 240);
    },
    [rowYs, state.widgets],
  );

  useEffect(() => {
    if (!present) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") exitPresent();
      else if (e.key === "ArrowRight" || e.key === " " || e.key === "Enter") {
        e.preventDefault();
        goto(step + 1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        goto(step - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [present, step, goto, exitPresent]);

  // Recurring entities (tickers, teams, products) keep one colour everywhere.
  const entities = useMemo(
    () => buildEntityColors(state.widgets, chartTheme.series),
    [state.widgets, chartTheme.series],
  );

  // Per-canvas identity chosen by the agent: accent, display voice, paper, card treatment.
  const style: CanvasStyle | null = state.canvas?.style ?? null;
  const DISPLAY: Record<string, string> = {
    serif: "'New York', 'Iowan Old Style', Georgia, 'Times New Roman', serif",
    mono: "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace",
    sans: "system-ui, -apple-system, 'Segoe UI', sans-serif",
  };
  // Paper tint is mode-aware: a light paper in dark mode would fight the theme
  // (topbar and cards stay dark while the board goes light), so each has a dark twin.
  const PAPER: Record<string, { light: string; dark: string }> = {
    default: { light: "", dark: "" },
    cream: { light: "#faf6ec", dark: "#131109" },
    cool: { light: "#f2f5f8", dark: "#0b0d11" },
    sage: { light: "#f2f6f1", dark: "#0b0e0b" },
    blush: { light: "#f9f3f1", dark: "#120d0c" },
  };
  const paper = style ? PAPER[style.paper]?.[mode] : "";
  const styleVars = style
    ? ({
        ...(style.accent ? { "--accent": style.accent, "--accent-ink": style.accent, "--accent-wash": `${style.accent}1c` } : {}),
        "--display": DISPLAY[style.type] ?? DISPLAY.sans,
        ...(paper ? { "--page": paper, "--page-2": paper } : {}),
      } as React.CSSProperties)
    : undefined;

  return (
    <FilterProvider>
    <div
      className={`stage ${present ? "presenting" : bare ? "bare" : railOpen ? "" : "wide"}`}
      data-cards={style?.cards ?? "bordered"}
      style={styleVars}
    >
      <div className="board">
        {empty && !busy && (
          <div className="board-empty">
            <div className="board-empty-inner">
              <div className="empty-mark" aria-hidden>◈</div>
              <h2 className="empty-title">A blank canvas.</h2>
              <p className="hint">
                Ask a question and the answer arrives composed — charts, metrics, and notes you can rearrange.
              </p>
              <div className="seeds">
                {PROMPTS.map((p) => (
                  <button key={p} onClick={() => send(p)}>
                    {p}
                    <span className="seed-arrow" aria-hidden>→</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        {!empty && !present && !bare && (
          <button className="present-launch" title="Present this canvas" onClick={enterPresent}>
            ▶ present
          </button>
        )}
        <Canvas
          widgets={state.widgets}
          entities={entities}
          facts={facts}
          revealY={revealY}
          onLayoutChange={applyOps}
          onRemove={(widgetId) => applyOps([{ kind: "remove_widget", widgetId }])}
        />
        {busy && empty && (
          <div className="board-empty">
            <div className="composing">composing</div>
          </div>
        )}
      </div>

      {!present && !bare && (
        <StreamRail messages={messages} busy={busy} open={railOpen} onToggle={() => setRailOpen((v) => !v)} />
      )}

      {present && (
        <div className="present-bar">
          <button onClick={() => goto(step - 1)} disabled={step === 0} aria-label="Previous">
            ‹
          </button>
          <span className="present-count">
            {step + 1} / {rowYs.length}
          </span>
          <button onClick={() => goto(step + 1)} disabled={step >= rowYs.length - 1} aria-label="Next">
            ›
          </button>
          <button className="present-exit" onClick={exitPresent}>
            exit
          </button>
        </div>
      )}

      <div className="dock" hidden={bare}>
        {error && <div className="dock-error">{error.message}</div>}
        <form
          className={`bar ${busy ? "busy" : ""}`}
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <span className="bar-glyph" aria-hidden>
            ◈
          </span>
          <textarea
            ref={box}
            rows={1}
            value={input}
            placeholder={busy ? "composing…" : "Ask anything. The answer arrives as a canvas."}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
          />
          <button type="submit" disabled={busy || !input.trim()}>
            {busy ? <span className="spin" /> : "↵"}
          </button>
        </form>
      </div>
    </div>
    </FilterProvider>
  );
}

const ICON = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;

function SunIcon() {
  return (
    <svg {...ICON} aria-hidden>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.4v2.4M12 19.2v2.4M4.22 4.22l1.7 1.7M18.08 18.08l1.7 1.7M2.4 12h2.4M19.2 12h2.4M4.22 19.78l1.7-1.7M18.08 5.92l1.7-1.7" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg {...ICON} aria-hidden>
      <path d="M20.5 14.2A8.2 8.2 0 1 1 10.3 3.7a6.4 6.4 0 0 0 10.2 10.5Z" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg {...ICON} aria-hidden>
      <path d="M12 3.5v11m0 0l-4-4m4 4l4-4M4.5 19.5h15" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg {...ICON} aria-hidden>
      <circle cx="12" cy="12" r="3.1" />
      <path d="M19.4 13.5a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 0 1-4 0v-.09A1.7 1.7 0 0 0 8.4 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H2.4a2 2 0 0 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.4a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1.03-1.56V2.4a2 2 0 0 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.56 1.03h.09a2 2 0 0 1 0 4h-.09a1.7 1.7 0 0 0-1.56 1.03Z" />
    </svg>
  );
}

export default function App() {
  const [canvases, setCanvases] = useState<CanvasListItem[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [navOpen, setNavOpen] = useState(false);
  const [setOpen, setSetOpen] = useState(false);
  const { mode, set } = useTheme();

  const [exportOpen, setExportOpen] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);

  const refreshList = useCallback(async () => {
    const res = await fetch("/api/canvases");
    setCanvases(await res.json());
  }, []);

  /** Rendering a tall board takes a few seconds, so fetch it rather than
   * navigating — that way the button can show progress and report a failure. */
  const exportCanvas = useCallback(
    async (format: "png" | "pdf") => {
      if (!active) return;
      setExporting(format);
      setExportOpen(false);
      try {
        const res = await fetch(`/api/canvases/${active}/export?format=${format}`);
        if (!res.ok) throw new Error(`export failed (${res.status})`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download =
          res.headers.get("Content-Disposition")?.match(/filename="(.+?)"/)?.[1] ??
          `canvas.${format}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        console.error(e);
        alert(`Could not export this canvas as ${format.toUpperCase()}.`);
      } finally {
        setExporting(null);
      }
    },
    [active],
  );

  useEffect(() => {
    refreshList();
  }, [refreshList, active, title]);

  const create = useCallback(async () => {
    const res = await fetch("/api/canvases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const c = await res.json();
    setTitle(c.title);
    setActive(c.id);
    setNavOpen(false);
    refreshList();
  }, [refreshList]);

  // Resume the most recent canvas on load; only create when there is nothing to open.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    const deep = new URLSearchParams(location.search).get("canvas");
    if (deep) {
      setActive(deep);
      fetch(`/api/canvases/${deep}`)
        .then((r) => r.json())
        .then((d) => d?.canvas?.title && setTitle(d.canvas.title))
        .catch(() => {});
      return;
    }
    fetch("/api/canvases")
      .then((r) => r.json())
      .then((list: CanvasListItem[]) => {
        if (list.length) {
          setActive(list[0].id);
          setTitle(list[0].title);
        } else {
          create();
        }
      });
  }, [create]);

  return (
    <div className="app">
      <div className="veil" aria-hidden />
      <header className="topbar">
        <button className="mark" onClick={() => setNavOpen((v) => !v)}>
          <span className="mark-glyph">◈</span> VISION
        </button>
        <span className="crumb">{title}</span>
        <div className="top-actions">
          <button
            className="icon"
            title={mode === "dark" ? "Switch to light" : "Switch to dark"}
            onClick={() => set({ mode: mode === "dark" ? "light" : "dark" })}
          >
            {mode === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
          <div className="export-wrap">
            <button
              className="icon"
              title="Export this canvas"
              aria-haspopup="menu"
              aria-expanded={exportOpen}
              disabled={!active || exporting !== null}
              onClick={() => setExportOpen((v) => !v)}
            >
              {exporting ? <span className="spin spin-dark" /> : <DownloadIcon />}
            </button>
            {exportOpen && (
              <>
                <div className="export-scrim" onClick={() => setExportOpen(false)} />
                <div className="export-menu" role="menu">
                  <button role="menuitem" onClick={() => exportCanvas("png")}>
                    PNG <em>image</em>
                  </button>
                  <button role="menuitem" onClick={() => exportCanvas("pdf")}>
                    PDF <em>document</em>
                  </button>
                </div>
              </>
            )}
          </div>
          <button className="icon" title="Settings" onClick={() => setSetOpen(true)}>
            <GearIcon />
          </button>
          <button onClick={() => active && fetch(`/api/canvases/${active}/undo`, { method: "POST" })}>undo</button>
          <button className="primary" onClick={create}>
            new canvas
          </button>
        </div>
      </header>

      {navOpen && (
        <div className="nav-sheet" onClick={() => setNavOpen(false)}>
          <div className="nav-inner" onClick={(e) => e.stopPropagation()}>
            <p className="nav-label">canvases</p>
            {canvases.map((c) => (
              <button
                key={c.id}
                data-canvas-id={c.id}
                className={c.id === active ? "active" : ""}
                onClick={() => {
                  setActive(c.id);
                  setTitle(c.title);
                  setNavOpen(false);
                }}
              >
                <span>{c.title}</span>
                <em>{c.widget_count}</em>
              </button>
            ))}
          </div>
        </div>
      )}

      {setOpen && <Settings onClose={() => setSetOpen(false)} />}

      {active && <CanvasView key={active} canvasId={active} onTitle={setTitle} />}
    </div>
  );
}

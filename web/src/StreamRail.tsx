import { useEffect, useMemo, useRef, useState } from "react";
import type { UIMessage } from "ai";

const VERB: Record<string, string> = {
  web_search: "searched the web",
  x_search: "searched X",
  code_execution: "ran code",
};

/** Which glyph rides a step's node. */
const GLYPH_FOR: Record<string, string> = {
  code_execution: "code",
  web_search: "search",
  x_search: "search",
};

/** tool name → category → past-tense verb, folded into the quiet summary line. */
function categoryOf(name: string): string {
  if (name === "web_search" || name === "x_search") return "query";
  if (name === "code_execution") return "compute";
  if (name === "create_chart" || name === "create_image") return "chart";
  if (name.startsWith("create_")) return "build";
  if (name === "set_style" || name === "set_layout" || name === "set_lanes") return "arrange";
  return "revise"; // update_*, add_chart_series, remove_chart_series, set_chart_*, retitle, delete
}

const VERB_PAST: Record<string, string> = {
  query: "Searched",
  compute: "Computed",
  chart: "Charted",
  build: "Built",
  arrange: "Arranged",
  revise: "Revised",
};
const CAT_ORDER = ["query", "compute", "chart", "build", "arrange", "revise"];

const GLYPHS: Record<string, string> = {
  chart: '<path d="M5 20V13M12 20V6M19 20V10"/>',
  build: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M10 10v10"/>',
  layout: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 12h16"/>',
  edit: '<path d="M5 19h4l9-9-4-4-9 9z"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M20 20l-5.2-5.2"/>',
  code: '<path d="M9 8l-4 4 4 4M15 8l4 4-4 4"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18"/>',
  check: '<path d="M4 12l5 5L20 6"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
};

function Glyph({ name, size = 12, className }: { name: string; size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
      dangerouslySetInnerHTML={{ __html: GLYPHS[name] ?? "" }}
    />
  );
}

type Kind = "you" | "say" | "think" | "act";

interface Entry {
  key: string;
  kind: Kind;
  text: string;
  count: number;
  sources: string[];
  glyph?: string;
  category?: string;
  lines?: number;
}

/** The rail is a voice-over, not a document — drop any markdown the model emits. */
function plain(s: string) {
  return s
    .replace(/```[\s\S]*?```/g, "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/(^|\s)\*(?!\s)(.+?)\*(?=\s|$)/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "· ")
    .trim();
}

function domain(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function build(messages: UIMessage[]): Entry[] {
  const out: Entry[] = [];
  const push = (
    kind: Kind,
    text: string,
    key: string,
    sources: string[] = [],
    glyph?: string,
    category?: string,
    lines?: number,
  ) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind && last.text === text) {
      last.count += 1;
      last.lines = (last.lines ?? 0) + (lines ?? 0);
      for (const s of sources) if (!last.sources.includes(s)) last.sources.push(s);
      return;
    }
    out.push({ key, kind, text, count: 1, sources: [...new Set(sources)], glyph, category, lines });
  };

  messages.forEach((m, mi) => {
    if (m.role === "user") {
      const t = m.parts.find((p) => p.type === "text") as { text?: string } | undefined;
      if (t?.text) push("you", t.text, `${mi}-u`);
      return;
    }

    m.parts.forEach((part, pi) => {
      const key = `${mi}-${pi}`;
      if (part.type === "text" && plain(part.text)) {
        push("say", plain(part.text), key);
      } else if (part.type === "reasoning" && plain(part.text)) {
        push("think", plain(part.text), key);
      } else if (part.type === "source-url") {
        const d = domain((part as { url: string }).url);
        const last = out[out.length - 1];
        if (d && last && last.kind === "act" && !last.sources.includes(d)) last.sources.push(d);
      } else if (part.type.startsWith("tool-")) {
        const name = part.type.slice(5);
        const p = part as { input?: any };
        const verb = VERB[name] ?? name.replace(/_/g, " ");
        const detail = p.input?.title ?? p.input?.query ?? p.input?.label ?? "";
        const lines =
          name === "code_execution" && typeof p.input?.code === "string"
            ? p.input.code.split("\n").filter((l: string) => l.trim()).length
            : 0;
        push("act", detail ? `${verb} — ${detail}` : verb, key, [], GLYPH_FOR[name] ?? "spark", categoryOf(name), lines);
      }
    });
  });
  return out;
}

interface Turn {
  you: Entry | null;
  items: Entry[];
}

function splitTurns(entries: Entry[]): Turn[] {
  const turns: Turn[] = [];
  for (const e of entries) {
    if (e.kind === "you" || turns.length === 0) {
      turns.push({ you: e.kind === "you" ? e : null, items: e.kind === "you" ? [] : [e] });
    } else {
      turns[turns.length - 1].items.push(e);
    }
  }
  return turns;
}

/** A turn's activity, read in order: narration paragraphs and runs of tool steps
 * between them — the same shape a reader gets from a coding agent's log. */
type Block = { type: "say"; entry: Entry } | { type: "steps"; entries: Entry[] };

function blocksOf(items: Entry[], showThinking: boolean): Block[] {
  const blocks: Block[] = [];
  let run: Entry[] = [];
  const flush = () => {
    if (run.length) blocks.push({ type: "steps", entries: run });
    run = [];
  };
  for (const e of items) {
    if (e.kind === "say") {
      flush();
      blocks.push({ type: "say", entry: e });
    } else if (e.kind === "think") {
      if (showThinking) run.push(e);
    } else {
      run.push(e);
    }
  }
  flush();
  return blocks;
}

function secs(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  const r = s < 100 ? Math.round(s * 10) / 10 : Math.round(s);
  return `${Number.isInteger(r) ? r.toFixed(0) : r.toFixed(1)}s`;
}

export function StreamRail({
  messages,
  busy,
  open,
  onToggle,
}: {
  messages: UIMessage[];
  busy: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const entries = useMemo(() => build(messages), [messages]);
  const turns = useMemo(() => splitTurns(entries), [entries]);
  const [showThinking, setShowThinking] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const end = useRef<HTMLDivElement>(null);
  const seenAt = useRef<Map<string, number>>(new Map());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = Date.now();
    for (const e of entries) if (!seenAt.current.has(e.key)) seenAt.current.set(e.key, t);
  }, [entries]);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries.length, busy]);

  useEffect(() => {
    if (!busy) return;
    const id = window.setInterval(() => setNow(Date.now()), 300);
    return () => window.clearInterval(id);
  }, [busy]);

  const at = (key?: string) => (key ? seenAt.current.get(key) : undefined);

  const renderLiveStep = (e: Entry, dur: number | null, isLast: boolean) => (
    <li key={e.key} className={`step step-${e.kind}`}>
      <span className="step-node" aria-hidden>
        {e.kind === "act" ? isLast ? <span className="step-spin" /> : <Glyph name="check" size={11} /> : null}
      </span>
      <span className="step-text">
        {e.kind === "act" && e.glyph && (
          <span className="step-glyph" aria-hidden>
            <Glyph name={e.glyph} size={12} />
          </span>
        )}
        {e.text}
        {e.count > 1 && <span className="entry-count">×{e.count}</span>}
        {e.sources.length > 0 && (
          <span className="entry-sources">
            {e.sources.slice(0, 4).map((s) => (
              <span key={s} className="src">
                {s}
              </span>
            ))}
            {e.sources.length > 4 && <span className="src more">+{e.sources.length - 4}</span>}
          </span>
        )}
      </span>
      {dur !== null && dur >= 400 && <span className="step-time">{secs(dur)}</span>}
    </li>
  );

  const renderStepsBlock = (turnIdx: number, blockIdx: number, entries: Entry[], live: boolean) => {
    const acts = entries.filter((e) => e.kind === "act");
    const roll = new Map<string, number>();
    let lines = 0;
    for (const e of acts) {
      roll.set(e.category ?? "revise", (roll.get(e.category ?? "revise") ?? 0) + e.count);
      lines += e.lines ?? 0;
    }
    const chips = CAT_ORDER.filter((c) => roll.has(c)).map((c) => `${VERB_PAST[c]} ${roll.get(c)}`);
    const key = `${turnIdx}-${blockIdx}`;
    const isOpen = live || expanded.has(key);

    if (live) {
      return (
        <ol key={key} className="steps">
          {entries.map((e, idx, arr) => {
            const nextAt = at(arr[idx + 1]?.key);
            const dur = at(e.key) != null ? (nextAt ?? now) - at(e.key)! : null;
            return renderLiveStep(e, dur, idx === arr.length - 1);
          })}
        </ol>
      );
    }

    // Closed group: one quiet summary line — no banner, just a check, what
    // happened, and how long it took — with an optional drill-down.
    const start = at(entries[0]?.key);
    const finish = at(entries[entries.length - 1]?.key);
    const dur = start != null && finish != null ? finish - start : null;

    return (
      <div key={key} className="step-group">
        <button
          className="step-summary"
          onClick={() =>
            setExpanded((s) => {
              const n = new Set(s);
              n.has(key) ? n.delete(key) : n.add(key);
              return n;
            })
          }
        >
          <Glyph name="check" size={12} className="step-summary-check" />
          <span className="step-summary-text">
            {chips.map((c, i) => (
              <span key={c}>
                {i > 0 && <span className="dim"> · </span>}
                {c}
              </span>
            ))}
            {lines > 0 && (
              <span className="dim">
                {" "}
                · +{lines} line{lines === 1 ? "" : "s"}
              </span>
            )}
          </span>
          {dur !== null && dur >= 400 && <span className="step-summary-time">{secs(dur)}</span>}
          <Glyph name="chevron" size={11} className={`step-summary-chevron ${isOpen ? "open" : ""}`} />
        </button>
        {isOpen && (
          <ol className="steps steps-nested">
            {entries.map((e, idx, arr) => {
              const nextAt = at(arr[idx + 1]?.key);
              const dur = at(e.key) != null && nextAt != null ? nextAt - at(e.key)! : null;
              return renderLiveStep(e, dur, false);
            })}
          </ol>
        )}
      </div>
    );
  };

  const renderTurn = (turn: Turn, i: number) => {
    const isActive = i === turns.length - 1 && busy;
    const blocks = blocksOf(turn.items, showThinking);
    const started = turn.items.length > 0 || !isActive;

    return (
      <section key={i} className="turn">
        {turn.you && <p className="turn-you">{turn.you.text}</p>}

        {isActive && !started && (
          <div className="turn-pulse">
            <span className="dots" aria-hidden>
              <i />
              <i />
              <i />
            </span>
            thinking
          </div>
        )}

        {blocks.map((b, bi) => {
          if (b.type === "say") {
            return (
              <p key={bi} className="turn-text">
                {b.entry.text}
              </p>
            );
          }
          const isLastBlock = bi === blocks.length - 1;
          return renderStepsBlock(i, bi, b.entries, isActive && isLastBlock);
        })}
      </section>
    );
  };

  return (
    <aside className={`rail ${open ? "" : "collapsed"}`}>
      <div className="rail-head">
        <button className="rail-toggle" onClick={onToggle} title={open ? "Hide stream" : "Show stream"}>
          <svg viewBox="0 0 24 24" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d={open ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} />
          </svg>
        </button>
        {open && (
          <>
            <span className="rail-title">Stream</span>
            <button
              className={`rail-filter ${showThinking ? "on" : ""}`}
              onClick={() => setShowThinking((v) => !v)}
              title={showThinking ? "Hide reasoning" : "Show reasoning"}
            >
              <svg viewBox="0 0 24 24" width={12} height={12} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                {showThinking ? (
                  <>
                    <path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z" />
                    <circle cx="12" cy="12" r="2.6" />
                  </>
                ) : (
                  <path d="M4 4l16 16M9.9 5.2A9.6 9.6 0 0 1 12 5c6.5 0 10 7 10 7a15 15 0 0 1-3 3.6M6.3 6.8A15 15 0 0 0 2 12s3.5 6.5 10 6.5a9.7 9.7 0 0 0 3-.45" />
                )}
              </svg>
              reasoning
            </button>
          </>
        )}
      </div>

      {open && (
        <div className="rail-body">
          {turns.length === 0 && !busy && (
            <p className="rail-empty">
              Reasoning, searches, and narration play out here. The answers land on the canvas.
            </p>
          )}
          {turns.map((turn, i) => renderTurn(turn, i))}
          <div ref={end} />
        </div>
      )}
    </aside>
  );
}

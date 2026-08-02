import { useEffect, useRef, useState } from "react";
import type { UIMessage } from "ai";

const TOOL_VERB: Record<string, string> = {
  add_chart: "charting",
  add_kpi: "pinning a metric",
  add_table: "tabulating",
  add_narrative: "annotating",
  generate_image: "imagining",
  update_widget: "revising",
  remove_widget: "clearing",
  web_search: "searching the web",
  x_search: "searching X",
  code_execution: "running code",
};

interface Entry {
  key: string;
  kind: "you" | "say" | "think" | "act";
  text: string;
  done?: boolean;
}

function toEntries(messages: UIMessage[]): Entry[] {
  const out: Entry[] = [];
  messages.forEach((m, mi) => {
    if (m.role === "user") {
      const t = m.parts.find((p) => p.type === "text") as { text?: string } | undefined;
      if (t?.text) out.push({ key: `${mi}-u`, kind: "you", text: t.text });
      return;
    }
    m.parts.forEach((part, pi) => {
      const key = `${mi}-${pi}`;
      if (part.type === "text" && part.text.trim()) {
        out.push({ key, kind: "say", text: part.text });
      } else if (part.type === "reasoning" && part.text.trim()) {
        out.push({ key, kind: "think", text: part.text });
      } else if (part.type.startsWith("tool-")) {
        const name = part.type.slice(5);
        const p = part as { state?: string; input?: any };
        const verb = TOOL_VERB[name] ?? name;
        let detail = "";
        if (p.input?.title) detail = p.input.title;
        else if (p.input?.query) detail = p.input.query;
        else if (p.input?.prompt) detail = String(p.input.prompt).slice(0, 60);
        out.push({
          key,
          kind: "act",
          text: detail ? `${verb} — ${detail}` : verb,
          done: p.state === "output-available" || p.state === "output-error",
        });
      }
    });
  });
  return out;
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
  const entries = toEntries(messages);
  const end = useRef<HTMLDivElement>(null);
  const [showThinking, setShowThinking] = useState(true);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries.length]);

  const visible = showThinking ? entries : entries.filter((e) => e.kind !== "think");

  return (
    <aside className={`rail ${open ? "" : "collapsed"}`}>
      <div className="rail-head">
        <button className="rail-toggle" onClick={onToggle} title={open ? "Hide stream" : "Show stream"}>
          {open ? "›" : "‹"}
        </button>
        {open && (
          <>
            <span className="rail-title">stream</span>
            <button
              className={`rail-filter ${showThinking ? "on" : ""}`}
              onClick={() => setShowThinking((v) => !v)}
            >
              thinking
            </button>
          </>
        )}
      </div>

      {open && (
        <div className="rail-body">
          {visible.length === 0 && !busy && (
            <p className="rail-empty">
              Reasoning, searches, and narration appear here. The answers appear on the canvas.
            </p>
          )}
          {visible.map((e) => (
            <div key={e.key} className={`entry ${e.kind}`}>
              <span className="entry-mark" aria-hidden />
              <span className="entry-text">{e.text}</span>
            </div>
          ))}
          {busy && (
            <div className="entry pulse">
              <span className="entry-mark" aria-hidden />
              <span className="entry-text">thinking</span>
            </div>
          )}
          <div ref={end} />
        </div>
      )}
    </aside>
  );
}

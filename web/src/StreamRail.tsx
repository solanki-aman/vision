import { useEffect, useMemo, useRef, useState } from "react";
import type { UIMessage } from "ai";

const VERB: Record<string, string> = {
  add_chart: "charted",
  add_kpi: "pinned a metric",
  add_table: "tabulated",
  add_narrative: "annotated",
  generate_image: "imagined",
  update_widget: "revised",
  remove_widget: "cleared",
  web_search: "searched the web",
  x_search: "searched X",
  code_execution: "ran code",
};

type Kind = "you" | "say" | "think" | "act";

interface Entry {
  key: string;
  kind: Kind;
  text: string;
  count: number;
  sources: string[];
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

/** Consecutive identical activities collapse into one row with a count. */
function build(messages: UIMessage[]): Entry[] {
  const out: Entry[] = [];
  const push = (kind: Kind, text: string, key: string, sources: string[] = []) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind && last.text === text) {
      last.count += 1;
      for (const s of sources) if (!last.sources.includes(s)) last.sources.push(s);
      return;
    }
    out.push({ key, kind, text, count: 1, sources: [...new Set(sources)] });
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
        const detail = p.input?.title ?? p.input?.query ?? "";
        push("act", detail ? `${verb} — ${detail}` : verb, key);
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
  const entries = useMemo(() => build(messages), [messages]);
  const [showThinking, setShowThinking] = useState(true);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries.length, busy]);

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
              <div className="entry-main">
                <span className="entry-text">
                  {e.text}
                  {e.count > 1 && <span className="entry-count">×{e.count}</span>}
                </span>
                {e.sources.length > 0 && (
                  <span className="entry-sources">
                    {e.sources.slice(0, 4).join(" · ")}
                    {e.sources.length > 4 ? ` +${e.sources.length - 4}` : ""}
                  </span>
                )}
              </div>
            </div>
          ))}

          {busy && (
            <div className="entry pulse">
              <span className="entry-mark" aria-hidden />
              <div className="entry-main">
                <span className="entry-text">working</span>
              </div>
            </div>
          )}
          <div ref={end} />
        </div>
      )}
    </aside>
  );
}

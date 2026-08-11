import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import { RESTRICTED, parseDocUrl, useCitedDocument, useDocuments } from "./DocumentContext";
import type { Fact } from "./types";

/** The widget whose drill-down is open, so a page preview it opens can name it. */
const CitedByCtx = createContext<string | undefined>(undefined);

function fmtNum(v?: number | null): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function withUnit(v: number | null | undefined, unit?: string | null): string {
  return `${fmtNum(v)}${unit ? ` ${unit}` : ""}`;
}

function domain(url?: string | null): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" aria-hidden>
      <circle cx="10.5" cy="10.5" r="6" />
      <path d="M20 20l-5.2-5.2" />
    </svg>
  );
}

function CodeIcon() {
  return (
    <svg viewBox="0 0 24 24" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 8l-4 4 4 4M15 8l4 4-4 4" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg viewBox="0 0 24 24" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 3H7a1.6 1.6 0 0 0-1.6 1.6v14.8A1.6 1.6 0 0 0 7 21h10a1.6 1.6 0 0 0 1.6-1.6V7.6Z" />
      <path d="M14 3v4.6h4.6" />
    </svg>
  );
}

/** A fact that came off a page of paper. The chip opens the page itself — a
 * quoted sentence would be the weaker evidence this design replaced (§5). */
function DocSource({ cited, asOf }: { cited: { docId: string; page: number }; asOf?: string | null }) {
  const { open } = useDocuments();
  const citedBy = useContext(CitedByCtx);
  const doc = useCitedDocument(cited.docId);

  // The number is still on the canvas; the paper behind it is not shared. Say
  // that plainly rather than offering a chip that 404s (§12).
  if (doc === RESTRICTED) {
    return (
      <div className="dd-src dd-src-restricted">
        <FileIcon /> source restricted by uploader
      </div>
    );
  }

  const filename = doc ? doc.filename : "document";
  return (
    <div className="dd-src">
      {asOf ? `as of ${asOf}` : ""}
      <button
        className="doc-cite"
        title={`Open ${filename} at page ${cited.page}`}
        onClick={() => open({ ...cited, citedBy })}
      >
        <FileIcon /> {filename} p{cited.page}
      </button>
    </div>
  );
}

function Source({ f }: { f: Fact }) {
  const cited = parseDocUrl(f.sourceUrl);
  if (cited) return <DocSource cited={cited} asOf={f.asOf} />;

  const d = domain(f.sourceUrl);
  if (!d && !f.asOf) return null;
  return (
    <div className="dd-src">
      {d}
      {f.asOf ? ` · as of ${f.asOf}` : ""}
      {f.sourceUrl && (
        <a href={f.sourceUrl} target="_blank" rel="noreferrer">
          source ↗
        </a>
      )}
    </div>
  );
}

function Retrieved({ f }: { f: Fact }) {
  const fromPaper = f.tool === "document";
  const label = fromPaper
    ? "read · from your document"
    : f.tool === "x_search"
      ? "retrieved · X search"
      : f.tool === "user"
        ? "provided · by you"
        : "retrieved · web search";
  return (
    <div className="dd-section">
      <div className="dd-section-head">
        {fromPaper ? <FileIcon /> : <SearchIcon />} {label}
      </div>
      {f.query && <div className="dd-query">{f.query}</div>}
      {f.snippet && <p className="dd-quote">“{f.snippet}”</p>}
      <Source f={f} />
    </div>
  );
}

function InputRow({ name, fact }: { name: string; fact?: Fact }) {
  return (
    <div className="dd-input">
      <div className="dd-input-top">
        <span className="dd-var">{name}</span>
        <span className="dd-input-label">{fact ? fact.label : "(input not found)"}</span>
        {fact && <span className="dd-input-val">{withUnit(fact.value, fact.unit)}</span>}
      </div>
      {fact?.snippet && <p className="dd-quote dd-quote-sm">“{fact.snippet}”</p>}
      {fact && <Source f={fact} />}
    </div>
  );
}

function Computed({ f, all }: { f: Fact; all: Record<string, Fact> }) {
  return (
    <>
      <div className="dd-section">
        <div className="dd-section-head">
          <SearchIcon /> inputs · from web search
        </div>
        {(f.inputs ?? []).map((inp) => (
          <InputRow key={inp.name} name={inp.name} fact={all[inp.factId]} />
        ))}
      </div>
      <div className="dd-section">
        <div className="dd-section-head">
          <CodeIcon /> computed · code interpreter
        </div>
        {f.formula && (
          <pre className="dd-code">
            <code>{f.formula}</code>
          </pre>
        )}
      </div>
      <div className="dd-result">
        <span className="dd-result-eq">=</span> {withUnit(f.value, f.unit)}
      </div>
    </>
  );
}

function FactBlock({ f, all, titled }: { f: Fact; all: Record<string, Fact>; titled: boolean }) {
  const computed = f.tool === "code_execution";
  return (
    <div className="dd-block">
      {titled && <div className="dd-block-title">{f.label}</div>}
      {computed ? <Computed f={f} all={all} /> : <Retrieved f={f} />}
    </div>
  );
}

export function ProvBadge({
  facts,
  all,
  headline,
  claimFacts = [],
  widgetTitle,
}: {
  facts: Fact[];
  all: Record<string, Fact>;
  /** The formatted value, shown at the top of the modal (e.g. the KPI number). */
  headline?: string;
  /** Facts the widget's title/prose was written from — sources for a claim, not a value. */
  claimFacts?: Fact[];
  /** Named in the page preview a document citation opens, so the page says what cited it. */
  widgetTitle?: string;
}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        return;
      }
      // Keep Tab inside the dialog while it's up, so keyboard users don't
      // wander into the canvas behind it.
      if (e.key !== "Tab" || !dialog.current) return;
      const focusable = dialog.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    // Move focus in on open, and hand it back to the (i) on close.
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLElement>(".dd-close")?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      (previous ?? trigger.current)?.focus?.();
    };
  }, [open]);

  if (!facts.length && !claimFacts.length) return null;

  const worst =
    facts.some((f) => f.confidence === "illustrative")
      ? "illustrative"
      : facts.some((f) => f.confidence === "estimated")
        ? "estimated"
        : "measured";
  // A widget with no bound numbers is here for its claim alone — don't let
  // `every()` on an empty list report it as "computed".
  const badge = !facts.length
    ? "sourced"
    : facts.every((f) => f.tool === "code_execution")
      ? "computed"
      : worst;
  const single = facts.length === 1;
  const value =
    headline ?? (single ? withUnit(facts[0].value, facts[0].unit) : "Provenance");

  return (
    <span className="prov-i-wrap">
      <button
        ref={trigger}
        className="prov-i"
        aria-label="How we got this number"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        <svg viewBox="0 0 24 24" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={1.7} aria-hidden>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5" strokeLinecap="round" />
          <circle cx="12" cy="7.6" r="0.4" fill="currentColor" stroke="none" />
        </svg>
      </button>
      {open && (
        <CitedByCtx.Provider value={widgetTitle}>
        <div className="dd-overlay" onClick={() => setOpen(false)}>
          <div
            className="dd-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            ref={dialog}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="dd-head">
              <div className="dd-head-main">
                <div className="dd-value" id={titleId}>
                  {value}
                </div>
                <div className="dd-sub">how this number was made</div>
              </div>
              <span className={`prov-badge prov-badge-${badge}`}>{badge}</span>
              <button className="dd-close" aria-label="Close" onClick={() => setOpen(false)}>
                ✕
              </button>
            </div>
            <div className="dd-body">
              {facts.map((f) => (
                <FactBlock key={f.factId} f={f} all={all} titled={!single} />
              ))}
              {claimFacts.length > 0 && (
                <div className="dd-block">
                  <div className="dd-section">
                    <div className="dd-section-head">
                      <SearchIcon /> the claim rests on
                    </div>
                    {claimFacts.map((f) => (
                      <InputRow key={f.factId} name={f.entity ?? "fact"} fact={f} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
        </CitedByCtx.Provider>
      )}
    </span>
  );
}

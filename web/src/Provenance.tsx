import { useEffect, useState } from "react";
import type { Fact } from "./types";

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

function Source({ f }: { f: Fact }) {
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
  const label = f.tool === "x_search" ? "retrieved · X search" : f.tool === "user" ? "provided · by you" : "retrieved · web search";
  return (
    <div className="dd-section">
      <div className="dd-section-head">
        <SearchIcon /> {label}
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
}: {
  facts: Fact[];
  all: Record<string, Fact>;
  /** The formatted value, shown at the top of the modal (e.g. the KPI number). */
  headline?: string;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!facts.length) return null;

  const worst =
    facts.some((f) => f.confidence === "illustrative")
      ? "illustrative"
      : facts.some((f) => f.confidence === "estimated")
        ? "estimated"
        : "measured";
  const badge = facts.every((f) => f.tool === "code_execution") ? "computed" : worst;
  const single = facts.length === 1;
  const value = headline ?? (single ? withUnit(facts[0].value, facts[0].unit) : "Provenance");

  return (
    <span className="prov-i-wrap">
      <button className="prov-i" aria-label="How we got this number" onClick={() => setOpen(true)}>
        <svg viewBox="0 0 24 24" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={1.7} aria-hidden>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5" strokeLinecap="round" />
          <circle cx="12" cy="7.6" r="0.4" fill="currentColor" stroke="none" />
        </svg>
      </button>
      {open && (
        <div className="dd-overlay" onClick={() => setOpen(false)}>
          <div className="dd-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="dd-head">
              <div className="dd-head-main">
                <div className="dd-value">{value}</div>
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
            </div>
          </div>
        </div>
      )}
    </span>
  );
}

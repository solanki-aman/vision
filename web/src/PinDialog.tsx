import { useEffect, useState } from "react";
import { PinIcon, CloseIcon, CheckIcon } from "./Icons";
import type { Widget } from "./types";

interface Preview {
  sections: { id: string; key: string; title: string }[];
  suggestedSection: string;
  shareMode: "live" | "snapshot";
  refreshability: { refreshable: number; frozen: number; frozenLabels: string[]; anyRefreshable: boolean };
  suggestedCadence: string;
  cadences: { clock: Record<string, string>; fiscal: Record<string, string>; event: Record<string, string> };
  bindings: { label: string; source: string; accessClass: string }[];
}

/**
 * The pin modal. It asks three things and *reports* two.
 *
 * Asks: section, cadence, and an optional watch condition. Reports: refreshability
 * (which bound numbers can actually be produced again) and share mode (derived from the
 * data's access class, never chosen) — because offering a daily refresh on a number
 * that cannot move, or letting someone believe a public tile shares privately, is the
 * kind of quiet lie that makes people distrust the whole surface.
 */
export function PinDialog({ widget, onClose, onPinned }: {
  widget: Widget;
  onClose: () => void;
  onPinned: () => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [section, setSection] = useState("");
  const [cadence, setCadence] = useState("");
  const [watchOn, setWatchOn] = useState(false);
  const [watchOp, setWatchOp] = useState<"below" | "above">("below");
  const [watchValue, setWatchValue] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`/api/home/pin-preview?widgetId=${widget.id}`)
      .then((r) => r.json())
      .then((p: Preview) => {
        setPreview(p);
        setSection(p.suggestedSection);
        setCadence(p.suggestedCadence);
      })
      .catch(() => {});
  }, [widget.id]);

  const pin = async () => {
    setBusy(true);
    const body: any = { widgetId: widget.id, sectionKey: section };
    if (cadence && cadence !== "manual") body.schedule = { kind: cadence };
    if (watchOn && watchValue.trim()) {
      body.watch = { path: "value", op: watchOp, value: Number(watchValue) };
    }
    try {
      const res = await fetch("/api/home/pins", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) onPinned();
    } finally {
      setBusy(false);
    }
  };

  const ref = preview?.refreshability;

  return (
    <div className="pin-scrim" onClick={onClose}>
      <div className="pin-dialog" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Pin to Home">
        <header className="pin-head">
          <span className="pin-head-icon"><PinIcon size={17} /></span>
          <div>
            <h2>Pin to Home</h2>
            <p className="pin-sub">{widget.title}</p>
          </div>
          <button className="pin-close" onClick={onClose} aria-label="Close"><CloseIcon size={16} /></button>
        </header>

        {!preview ? (
          <div className="pin-loading">Reading this tile’s data…</div>
        ) : (
          <div className="pin-body">
            {/* ASK: section */}
            <label className="pin-field">
              <span className="pin-label">Section</span>
              <div className="pin-chips">
                {preview.sections.filter((s) => s.key !== "brief").map((s) => (
                  <button
                    key={s.id}
                    className={`pin-chip ${section === s.key ? "on" : ""}`}
                    onClick={() => setSection(s.key)}
                  >
                    {s.title}
                  </button>
                ))}
              </div>
            </label>

            {/* ASK: cadence — when to look */}
            <label className="pin-field">
              <span className="pin-label">Refresh <em>when to look</em></span>
              <select className="pin-select" value={cadence} onChange={(e) => setCadence(e.target.value)}>
                <optgroup label="Clock">
                  {Object.entries(preview.cadences.clock).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </optgroup>
                <optgroup label="Fiscal calendar">
                  {Object.entries(preview.cadences.fiscal).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </optgroup>
                <optgroup label="Event">
                  {Object.entries(preview.cadences.event).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </optgroup>
              </select>
              {cadence === "source_update" && (
                <span className="pin-note ok">The only cadence that cannot be wrong — it fires when the data moves.</span>
              )}
            </label>

            {/* ASK: watch — when to speak */}
            <div className="pin-field">
              <label className="pin-watch-toggle">
                <input type="checkbox" checked={watchOn} onChange={(e) => setWatchOn(e.target.checked)} />
                <span className="pin-label">Watch condition <em>when to speak</em></span>
              </label>
              {watchOn && (
                <div className="pin-watch">
                  <span>Tell me if this goes</span>
                  <select value={watchOp} onChange={(e) => setWatchOp(e.target.value as any)} className="pin-select sm">
                    <option value="below">below</option>
                    <option value="above">above</option>
                  </select>
                  <input
                    className="pin-input"
                    type="number"
                    placeholder="e.g. 36"
                    value={watchValue}
                    onChange={(e) => setWatchValue(e.target.value)}
                  />
                </div>
              )}
            </div>

            {/* REPORT: refreshability + share mode */}
            <div className="pin-report">
              {ref && (
                <div className={`pin-stat ${ref.anyRefreshable ? "" : "warn"}`}>
                  <CheckIcon size={14} />
                  <span>
                    {ref.refreshable} of {ref.refreshable + ref.frozen} numbers re-runnable
                    {ref.frozen > 0 && <em> · {ref.frozen} frozen ({ref.frozenLabels.join(", ")})</em>}
                  </span>
                </div>
              )}
              <div className={`pin-stat share-${preview.shareMode}`}>
                <span className="pin-share-dot" aria-hidden />
                <span>
                  {preview.shareMode === "live"
                    ? "Shares live — recipients see their own entitled data."
                    : "Shares as a snapshot — these are public figures."}
                </span>
              </div>
            </div>
          </div>
        )}

        <footer className="pin-foot">
          <button className="pin-cancel" onClick={onClose}>Cancel</button>
          <button className="pin-confirm" onClick={pin} disabled={busy || !preview}>
            {busy ? "Pinning…" : "Pin to Home"}
          </button>
        </footer>
      </div>
    </div>
  );
}

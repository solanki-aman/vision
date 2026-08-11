import { useEffect, useState } from "react";

interface SharedDocument {
  docId: string;
  filename: string;
  /** How many facts on this canvas rest on the document — the disclosure. */
  factCount: number;
}

type Scope = "canvas" | "uploader";

const ROLES = ["viewer", "editor", "owner"] as const;

/**
 * The consent gate (§12).
 *
 * Sharing a canvas that cites documents requires an explicit decision about
 * each one, made with the fact counts in view. There is no default and no
 * "share everything" shortcut: silence is never consent, so the button stays
 * disabled until every listed document has been chosen for.
 */
export function ShareDialog({ canvasId, onClose }: { canvasId: string; onClose: () => void }) {
  const [documents, setDocuments] = useState<SharedDocument[] | null>(null);
  const [choices, setChoices] = useState<Record<string, Scope>>({});
  const [principal, setPrincipal] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("viewer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let live = true;
    fetch(`/api/canvases/${canvasId}/share-preview`, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(`could not load the share preview (${r.status})`);
        return r.json() as Promise<{ documents: SharedDocument[] }>;
      })
      .then((d) => live && setDocuments(d.documents ?? []))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [canvasId]);

  const undecided = (documents ?? []).filter((d) => !choices[d.docId]).length;
  const ready = documents !== null && undecided === 0 && principal.trim() !== "" && !busy;

  const submit = async () => {
    if (!documents) return;
    setBusy(true);
    setError(null);
    try {
      // The grant map is keyed by filename, which §2 guarantees is unique per
      // canvas — the same handle the model and the citations use.
      const decisions: Record<string, Scope> = {};
      for (const d of documents) decisions[d.filename] = choices[d.docId];
      const res = await fetch(`/api/canvases/${canvasId}/grants`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ principal: principal.trim(), role, documents: decisions }),
      });
      if (!res.ok) throw new Error(`share failed (${res.status})`);
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-panel" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Share this canvas</h2>
          <button onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <section>
          <p className="set-label">Share with</p>
          <div className="share-who">
            <input
              className="share-principal"
              value={principal}
              placeholder="user:someone@example.com"
              aria-label="Person or group to share with"
              onChange={(e) => setPrincipal(e.target.value)}
            />
            <select value={role} aria-label="Role" onChange={(e) => setRole(e.target.value as typeof role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
        </section>

        <section>
          <p className="set-label">Documents on this canvas</p>
          {documents === null && !error && <p className="set-hint">Loading…</p>}
          {documents?.length === 0 && (
            <p className="set-hint">No uploaded documents — nothing extra is disclosed by sharing.</p>
          )}
          {documents && documents.length > 0 && (
            <>
              <p className="set-hint">
                Excluding a document withholds the evidence, not the finding: its numbers stay on the
                canvas and its citations read as unverifiable.
              </p>
              <div className="share-docs">
                {documents.map((d) => (
                  <div key={d.docId} className="share-doc">
                    <div className="share-doc-head">
                      <strong>{d.filename}</strong>
                      <em>
                        {d.factCount} {d.factCount === 1 ? "fact" : "facts"} rest on it
                      </em>
                    </div>
                    <div className="share-doc-choice">
                      {(
                        [
                          ["canvas", "share with canvas"],
                          ["uploader", "keep private to me"],
                        ] as const
                      ).map(([scope, label]) => (
                        <label key={scope} className={choices[d.docId] === scope ? "on" : ""}>
                          <input
                            type="radio"
                            name={`share-${d.docId}`}
                            checked={choices[d.docId] === scope}
                            onChange={() => setChoices((prev) => ({ ...prev, [d.docId]: scope }))}
                          />
                          <span>{label}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>

        <section>
          {error && <p className="share-error">{error}</p>}
          {done && <p className="share-done">Shared. The decision is on the audit record.</p>}
          {!done && undecided > 0 && (
            <p className="set-hint">
              {undecided} {undecided === 1 ? "document still needs" : "documents still need"} a decision.
            </p>
          )}
          <div className="set-row">
            <button className="btn" disabled={!ready || done} onClick={submit}>
              {busy ? "Sharing…" : "Share"}
            </button>
            <button className="btn ghost" onClick={onClose}>
              {done ? "Close" : "Cancel"}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { RESTRICTED, useCitedDocument, type PageTarget } from "./DocumentContext";

/** Reading size. The renders table is keyed on dpi, so this caches alongside
 * the model's own 110 dpi render rather than displacing it (§5). */
const READ_DPI = 150;

/**
 * A page of an uploaded document at reading size.
 *
 * Nothing is highlighted on the page. A box drawn around "the number" would be
 * a VLM guess presented as evidence, which is the failure the images-only
 * design exists to remove — so the preview opens at the cited page and stops
 * there (§5).
 */
export function PagePreview({ target, onClose }: { target: PageTarget; onClose: () => void }) {
  const doc = useCitedDocument(target.docId);
  const [page, setPage] = useState(target.page);
  const [jump, setJump] = useState(String(target.page));
  const [broken, setBroken] = useState(false);
  const titleId = useId();
  const jumpId = useId();
  const dialog = useRef<HTMLDivElement>(null);

  const restricted = doc === RESTRICTED;
  const filename = doc && doc !== RESTRICTED ? doc.filename : "document";
  const pageCount = doc && doc !== RESTRICTED ? doc.pageCount : 0;

  const go = useCallback(
    (n: number) => {
      const clamped = Math.max(1, pageCount ? Math.min(pageCount, n) : n);
      setPage(clamped);
      setJump(String(clamped));
      setBroken(false);
    },
    [pageCount],
  );

  // Focus moves in on open and back to the citation on close — once, not on
  // every page turn.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLElement>(".dd-close")?.focus();
    return () => previous?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.tagName === "INPUT";
      const handled =
        e.key === "Escape" || e.key === "Tab" || (!typing && (e.key === "ArrowLeft" || e.key === "ArrowRight"));
      if (!handled) return;
      // Captured on the way down and stopped here: the provenance modal this
      // was opened from also listens on window, and Escape must close the
      // preview alone rather than both at once.
      e.stopPropagation();

      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        go(page - 1);
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        go(page + 1);
        return;
      }
      // Keep Tab inside the dialog, as the provenance drill-down does.
      if (!dialog.current) return;
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
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [page, go, onClose]);

  return (
    <div className="doc-overlay" onClick={onClose}>
      <div
        className="doc-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialog}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dd-head">
          <div className="dd-head-main">
            <div className="doc-title" id={titleId}>
              {filename}
            </div>
            <div className="dd-sub">
              page {page} of {pageCount || "?"}
            </div>
          </div>
          {!restricted && (
            <a className="doc-original" href={`/api/documents/${target.docId}/original`} download>
              original ↓
            </a>
          )}
          <button className="dd-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="doc-sheet">
          {restricted ? (
            <p className="doc-blocked">source restricted by uploader</p>
          ) : broken ? (
            <p className="doc-blocked">page {page} could not be loaded</p>
          ) : (
            <img
              key={page}
              src={`/api/documents/${target.docId}/pages/${page}?dpi=${READ_DPI}`}
              alt={`${filename}, page ${page}`}
              onError={() => setBroken(true)}
            />
          )}
        </div>

        <div className="doc-bar">
          <button onClick={() => go(page - 1)} disabled={page <= 1} aria-label="Previous page">
            ‹
          </button>
          <span className="doc-count" aria-live="polite">
            page {page} of {pageCount || "?"}
          </span>
          <button
            onClick={() => go(page + 1)}
            disabled={pageCount > 0 && page >= pageCount}
            aria-label="Next page"
          >
            ›
          </button>
          <form
            className="doc-jump"
            onSubmit={(e) => {
              e.preventDefault();
              const n = Number(jump);
              if (Number.isFinite(n) && n >= 1) go(Math.trunc(n));
              else setJump(String(page));
            }}
          >
            <label htmlFor={jumpId}>go to</label>
            <input
              id={jumpId}
              value={jump}
              inputMode="numeric"
              aria-label={`Jump to a page${pageCount ? ` between 1 and ${pageCount}` : ""}`}
              onChange={(e) => setJump(e.target.value.replace(/[^0-9]/g, ""))}
            />
          </form>
        </div>

        {target.citedBy && (
          <div className="doc-cited">
            cited by <strong>{target.citedBy}</strong>
          </div>
        )}
      </div>
    </div>
  );
}

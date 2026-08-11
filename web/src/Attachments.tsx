import { useCallback, useRef, useState, type DragEvent } from "react";
import { useDocuments, type DocumentRef } from "./DocumentContext";

/** Only PDF renders to page images today — office formats wait for the render
 * path rather than getting a text-extractor exception (§14). */
const ACCEPT = "application/pdf,.pdf";

function ClipIcon() {
  return (
    <svg viewBox="0 0 24 24" width={15} height={15} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 11.5l-7.8 7.8a4.6 4.6 0 0 1-6.5-6.5l8-8a3.1 3.1 0 0 1 4.4 4.4l-8 8a1.6 1.6 0 0 1-2.2-2.2l7.3-7.3" />
    </svg>
  );
}

function pages(doc: DocumentRef): string {
  if (!doc.pageCount) return "";
  return doc.pageCount === 1 ? "1 page" : `${doc.pageCount} pages`;
}

/**
 * Drag-and-drop onto the composer. `upload` is passed in rather than read from
 * context because the composer's owner is the component that mounts the
 * provider, so it sits above it.
 *
 * dragenter/dragleave fire again for every child element the pointer crosses,
 * so the highlight is driven by a depth count rather than by the first leave.
 */
export function useComposerDrop(upload: (files: Iterable<File>) => void) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);

  const onDragEnter = useCallback((e: DragEvent<HTMLElement>) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    depth.current += 1;
    setOver(true);
  }, []);

  const onDragLeave = useCallback(() => {
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setOver(false);
  }, []);

  const onDragOver = useCallback((e: DragEvent<HTMLElement>) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  const onDrop = useCallback(
    (e: DragEvent<HTMLElement>) => {
      e.preventDefault();
      depth.current = 0;
      setOver(false);
      if (e.dataTransfer.files.length) upload(Array.from(e.dataTransfer.files));
    },
    [upload],
  );

  return { over, dropProps: { onDragEnter, onDragLeave, onDragOver, onDrop } };
}

/** The paperclip, for people who would rather not drag. */
export function AttachButton() {
  const { upload, uploading } = useDocuments();
  const picker = useRef<HTMLInputElement>(null);

  return (
    <>
      <input
        ref={picker}
        className="sr-only"
        type="file"
        accept={ACCEPT}
        multiple
        // The paperclip is the labelled control; this is only its mechanism.
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          if (e.target.files?.length) upload(Array.from(e.target.files));
          // Let the same file be picked twice in a row.
          e.target.value = "";
        }}
      />
      <button
        type="button"
        className="attach"
        // Say where the pages go before they go there, at the point of upload
        // rather than in a policy (§11).
        title="Attach a PDF — every page is sent to Grok as an image"
        aria-label="Attach a PDF. Every page is sent to Grok as an image."
        disabled={uploading}
        onClick={() => picker.current?.click()}
      >
        {uploading ? <span className="spin spin-dark" /> : <ClipIcon />}
      </button>
    </>
  );
}

/** One chip per attached document, above the composer. */
export function AttachmentChips() {
  const { documents, uploadError, remove, open } = useDocuments();
  if (!documents.length && !uploadError) return null;

  return (
    <div className="doc-strip">
      {documents.map((d) => (
        <span key={d.docId} className={`doc-chip doc-chip-${d.status}`}>
          <button
            type="button"
            className="doc-chip-open"
            // A failed document has no page to show; the reason lives on hover.
            title={d.status === "failed" ? (d.error ?? "ingest failed") : `Open ${d.filename}`}
            disabled={d.status === "failed"}
            onClick={() => open({ docId: d.docId, page: 1 })}
          >
            {d.status === "pending" ? (
              <span className="spin spin-dark" aria-hidden />
            ) : (
              <span className="doc-chip-mark" aria-hidden>
                {d.status === "ready" ? "✓" : "!"}
              </span>
            )}
            <span className="doc-chip-name">{d.filename}</span>
            {pages(d) && <em>{pages(d)}</em>}
          </button>
          <button
            type="button"
            className="doc-chip-x"
            aria-label={`Remove ${d.filename}`}
            onClick={() => remove(d.docId)}
          >
            ✕
          </button>
        </span>
      ))}
      {uploadError && <span className="doc-chip doc-chip-failed doc-chip-flat">{uploadError}</span>}
    </div>
  );
}

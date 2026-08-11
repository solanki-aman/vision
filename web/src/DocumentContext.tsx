import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type DocumentStatus = "pending" | "ready" | "failed";

/** How a document is named everywhere: upload response, list, citation chip. */
export interface DocumentRef {
  docId: string;
  filename: string;
  pageCount: number;
  mediaType: string;
  status: DocumentStatus;
  error?: string | null;
}

/** What the page preview is showing, and what sent the reader there. */
export interface PageTarget {
  docId: string;
  page: number;
  /** Widget whose citation opened this, named in the preview footer. */
  citedBy?: string;
}

/** A document this viewer may not read — §12's "degrade loudly" case. */
export const RESTRICTED = "restricted";
export type Resolved = DocumentRef | typeof RESTRICTED;

export interface DocumentStore {
  documents: DocumentRef[];
  uploading: boolean;
  uploadError: string | null;
  refresh: () => Promise<void>;
  upload: (files: Iterable<File>) => Promise<void>;
  remove: (docId: string) => Promise<void>;
  /** Cited documents, probed on demand; undefined while the probe is in flight. */
  resolved: Record<string, Resolved>;
  resolve: (docId: string) => void;
  target: PageTarget | null;
  open: (target: PageTarget) => void;
  close: () => void;
}

// Every request rides the HttpOnly session cookie (§13) — no auth header exists
// to set, and `<img src>` and EventSource could not carry one anyway.
const AUTHED: RequestInit = { credentials: "same-origin" };

const NOOP: DocumentStore = {
  documents: [],
  uploading: false,
  uploadError: null,
  refresh: async () => {},
  upload: async () => {},
  remove: async () => {},
  resolved: {},
  resolve: () => {},
  target: null,
  open: () => {},
  close: () => {},
};

const DocCtx = createContext<DocumentStore>(NOOP);

export function DocumentProvider({ value, children }: { value: DocumentStore; children: ReactNode }) {
  return <DocCtx.Provider value={value}>{children}</DocCtx.Provider>;
}

export const useDocuments = () => useContext(DocCtx);

/**
 * The canvas's documents, its uploader, and the page-preview target.
 *
 * `refresh` is deliberately not self-triggering: the canvas already refetches on
 * every SSE tick, so it drives this too rather than opening a second channel.
 */
export function useDocumentStore(canvasId: string): DocumentStore {
  const [documents, setDocuments] = useState<DocumentRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [resolved, setResolved] = useState<Record<string, Resolved>>({});
  const [target, setTarget] = useState<PageTarget | null>(null);
  // Every citation on the canvas asks about its document on mount, and many of
  // them point at the same file — probe each id once.
  const probing = useRef(new Set<string>());

  const absorb = useCallback((rows: DocumentRef[]) => {
    setDocuments(rows);
    // The canvas list is authoritative for anything in it, so let it correct a
    // probe that ran while ingest was still pending.
    setResolved((prev) => {
      const next = { ...prev };
      for (const d of rows) next[d.docId] = d;
      return next;
    });
  }, []);

  const refresh = useCallback(async () => {
    const res = await fetch(`/api/canvases/${canvasId}/documents`, AUTHED);
    if (!res.ok) return;
    absorb((await res.json()) as DocumentRef[]);
  }, [canvasId, absorb]);

  const upload = useCallback(
    async (files: Iterable<File>) => {
      setUploadError(null);
      setUploading(true);
      try {
        for (const file of files) {
          const body = new FormData();
          body.append("file", file);
          const res = await fetch(`/api/canvases/${canvasId}/documents`, {
            ...AUTHED,
            method: "POST",
            body,
          });
          if (!res.ok) throw new Error(`${file.name} — upload failed (${res.status})`);
          const doc = (await res.json()) as DocumentRef;
          // Upload returns once the bytes are stored, so the chip can appear now;
          // the SSE tick that follows ingest is what flips it to ready.
          setDocuments((prev) => [...prev.filter((d) => d.docId !== doc.docId), doc]);
        }
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : String(e));
      } finally {
        setUploading(false);
      }
    },
    [canvasId],
  );

  const remove = useCallback(async (docId: string) => {
    await fetch(`/api/documents/${docId}`, { ...AUTHED, method: "DELETE" });
    setDocuments((prev) => prev.filter((d) => d.docId !== docId));
    setTarget((prev) => (prev?.docId === docId ? null : prev));
  }, []);

  const resolve = useCallback((docId: string) => {
    if (probing.current.has(docId)) return;
    probing.current.add(docId);
    fetch(`/api/documents/${docId}`, AUTHED)
      .then(async (res): Promise<Resolved> => {
        // An authorization miss answers 404 rather than 403 (§13), and either
        // way the reader cannot open the page — so any failure reads as
        // restricted. A citation that looks live and then 404s is the outcome
        // §12 exists to prevent.
        if (!res.ok) return RESTRICTED;
        return (await res.json()) as DocumentRef;
      })
      .catch((): Resolved => RESTRICTED)
      .then((r) => setResolved((prev) => ({ ...prev, [docId]: r })));
  }, []);

  const open = useCallback((next: PageTarget) => setTarget(next), []);
  const close = useCallback(() => setTarget(null), []);

  return useMemo(
    () => ({
      documents,
      uploading,
      uploadError,
      refresh,
      upload,
      remove,
      resolved,
      resolve,
      target,
      open,
      close,
    }),
    [documents, uploading, uploadError, refresh, upload, remove, resolved, resolve, target, open, close],
  );
}

/** `doc://{docId}#p{page}` — the source_url a fact extracted from paper carries. */
export function parseDocUrl(url?: string | null): { docId: string; page: number } | null {
  const m = /^doc:\/\/([^#]+)#p(\d+)$/.exec(url ?? "");
  return m ? { docId: m[1], page: Number(m[2]) } : null;
}

/** The cited document, probing for it the first time anything asks. */
export function useCitedDocument(docId: string): Resolved | undefined {
  const { resolved, resolve } = useDocuments();
  useEffect(() => resolve(docId), [docId, resolve]);
  return resolved[docId];
}

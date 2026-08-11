# Document ingest — design

Uploaded files reach the model as **page images and nothing else**. No text
extractor sits between the paper and the canvas. This document covers the storage
layer, the HTTP surface, how images are presented so the model can cite them, the
Pydantic types that carry a document through the code, and how context is budgeted
and shed when it overflows.

Read `data-provenance-design.md` first — every number a document contributes lands
in `canvas.facts` under the same rules as a searched one.

## 1. Why images

Measured against ground truth on grok-4.5 (fixtures and eval scripts reproduce it):

| path | tokens | table cells correct |
| --- | --- | --- |
| MarkItDown 0.1.7 text | 887 | **4 / 16** |
| page image @36dpi | 382 | 9 / 16 |
| **page image @45dpi** | **444** | **16 / 16** |
| 4-up sheet @45dpi | 996 (249/page) | 16 / 16 |
| dense 9pt 4-up @62dpi | 1681 (420/page) | 3 / 3 figures |

MarkItDown's pdfplumber path shifted every value one column right — `Industrial |
812.4 | 947.1 | 16.6%` became `Industrial | (blank) | 812.4 | 947.1`, so FY25
revenue reads as FY24. It is not that text is lossy; it is that text is lossy
*silently*, and a shifted column still arrives with a snippet and a source, which
is exactly what the provenance rules check. Pixels remove the failure mode rather
than mitigating it.

Images have their own floor — at 36 dpi the model returned `450.0` for `455.0` and
`-6.7%` for `-2.1%`, equally confident. So resolution is a fixed policy, not a
guess (§6).

### The token model

Seven measured sizes fit this within ~1%:

```
tokens ≈ 256 + (pixels / 1000)
```

Linear in area, **no cap**. The widely repeated "448×448 tiles, 1792 max" is not in
xAI's documentation and is contradicted by measurement (1275×1650 → 2328 tokens).
Every budget below is computed from this formula, so cost is known before a request
is built rather than discovered from an API error.

## 2. Types

`server/app/documents.py`, built on the existing `Spec` base (`extra="forbid"`), so
tool schemas and validation come from one definition exactly as `specs.py` does.

```python
class DocumentRef(Spec):
    """How a document is named everywhere: tool args, citations, UI."""
    docId: str
    filename: str
    pageCount: int
    mediaType: str

class PageRef(Spec):
    docId: str
    filename: str
    page: int          # 1-based — what a reader sees printed on the page

    def cite(self) -> str:
        return f"{self.filename} p{self.page}"

class PageImage(Spec):
    ref: PageRef
    dpi: int
    width: int
    height: int
    tokens: int        # 256 + w*h//1000, from §1
    objectKey: str

class SheetImage(Spec):
    """A contact sheet: several pages tiled, in a stated reading order."""
    docId: str
    filename: str
    pages: list[int]
    cols: int
    dpi: int
    width: int
    height: int
    tokens: int
    objectKey: str

class SectionNote(Spec):
    pages: str         # "12-18"
    what: str          # "quarterly segment tables"

class DocumentDigest(Spec):
    """What survives into later turns. Model-authored, never parser-derived."""
    docId: str
    filename: str
    pageCount: int
    thesis: str = Field(description="One sentence: what this document is.")
    sections: list[SectionNote]
```

`PageRef` is the unit of citation. Anything that can be cited constructs one; there
is no second way to spell a page reference.

**The filename is the model-facing handle; `docId` is internal.** The model reads
`[northwind-fy25.pdf p12]` on a label, writes the same string in prose, and passes
the same string to `view_pages` — one identifier end to end, so there is nothing to
transpose. `docId` appears only in `source_url` and in storage keys, where the model
never sees it. This requires filenames to be unique per canvas: upload suffixes a
collision (`report (2).pdf`) before the row is written.

## 3. Storage

MinIO, added to `docker-compose.yml` as a `minio` service with a `documents`
bucket. Keys:

```
{canvasId}/{docId}/original.pdf
{canvasId}/{docId}/page/{page}@{dpi}.jpg
{canvasId}/{docId}/sheet/{first}-{last}@{dpi}.jpg
```

**Presigned URLs do not work here.** MinIO is only reachable inside the compose
network, so xAI cannot fetch them. Images go to the model as base64 data URLs; the
object store exists to avoid re-rasterising, not to serve the model. Budget the
wire cost accordingly — base64 inflates bytes by 33% (tokens are unaffected).

```sql
CREATE SCHEMA IF NOT EXISTS documents;

CREATE TABLE documents.files (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canvas_id   UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  filename    TEXT NOT NULL,
  media_type  TEXT NOT NULL,
  byte_size   BIGINT NOT NULL,
  sha256      TEXT NOT NULL,
  page_count  INT,
  object_key  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',   -- pending | ready | failed
  error       TEXT,
  digest      JSONB,                             -- DocumentDigest, after turn 1
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON documents.files (canvas_id, created_at);

CREATE TABLE documents.renders (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id     UUID NOT NULL REFERENCES documents.files(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,        -- 'page' | 'sheet'
  first_page  INT NOT NULL,
  last_page   INT NOT NULL,
  cols        INT NOT NULL DEFAULT 1,
  dpi         INT NOT NULL,
  width       INT NOT NULL,
  height      INT NOT NULL,
  tokens      INT NOT NULL,
  object_key  TEXT NOT NULL,
  UNIQUE (file_id, kind, first_page, last_page, dpi)
);
```

The unique constraint makes rendering idempotent: ask for the same pages at the
same dpi twice and the second call is a lookup.

Rasterising is pypdfium2 (Apache-2.0, 7.8 MB, 0.048s for 3 pages — pixel-identical
output to PyMuPDF at 58 MB and AGPL). It renders and reads the outline; that is the
whole dependency.

**All pdfium work runs on one dedicated thread.** pdfium is not thread-safe, and
`asyncio.to_thread` spreads calls across an arbitrary pool — a background ingest
overlapping a `view_pages` render killed the worker process outright, with no
exception and no traceback, just a dead child and a server that stopped answering.
`documents.in_render_thread` is a single-worker executor that every call goes
through: still off the event loop, never concurrent. Two tests pin it, because the
failure it prevents is invisible in logs.

## 4. HTTP surface

```
POST   /api/canvases/{canvas_id}/documents     multipart -> DocumentRef, status=pending
GET    /api/canvases/{canvas_id}/documents     -> [DocumentRef]
GET    /api/documents/{doc_id}                 -> DocumentRef + digest + status
GET    /api/documents/{doc_id}/pages/{page}    -> image/jpeg  (?dpi=, proxied from MinIO)
GET    /api/documents/{doc_id}/original        -> the file as uploaded
DELETE /api/documents/{doc_id}
```

Upload returns as soon as the bytes are stored and the page count is known. Contact
sheets render in a background task; on completion the row flips to `ready` and the
handler calls `events.notify(canvas_id)` — the canvas already has an SSE channel
open, so the composer un-greys itself with no new transport and no polling.

`/api/chat` is unchanged. The agent re-derives attached documents from
`documents.files WHERE canvas_id = $1`, exactly as it re-derives canvas state,
rather than trusting the message history. The client still puts an AI SDK
`FileUIPart` (`{type:"file", filename, mediaType, url:"/api/documents/{id}"}`) in
the user message, but only so the conversation renders an attachment chip;
`to_lc_messages` ignores it as it ignores every non-text part today.

## 5. Presenting images so they can be cited

Every image is preceded by a text block naming it. The label is a caption for what
follows, and it is the exact string the model is asked to cite.

Single page:

```
[northwind-fy25.pdf p4]
<image>
```

Contact sheet — the reading order has to be stated or the grid is uncitable:

```
[northwind-fy25.pdf pages 5-8 · 2x2 grid · reading order: p5 top-left,
 p6 top-right, p7 bottom-left, p8 bottom-right]
<image>
```

**Each tile is also stamped with its page number in the pixels.** Stating the reading
order in the label still asks the model to map a grid position onto a number, and it
gets that wrong: the first end-to-end run produced a digest placing the freight chart
on p3 when it is on p4. Printing "p4" above the tile removed the inference and the
mapping came back correct. It costs about 40 tokens a sheet.

The skill gains one rule: *cite a document figure as `[filename p12]`; to read a
page closely, call `view_pages` on it.* Facts extracted from a document are stored
with `tool='document'` and `source_url='doc://{docId}#p{page}'`, which the
provenance drill-down resolves back to the page image — so clicking a number shows
the patch of paper it came from rather than a quoted sentence.

### The reader has to be able to read it

A thumbnail in the drill-down is not provenance, it is decoration. The claim this
design makes — that a page image is stronger evidence than a quoted sentence — only
holds if a person can actually check the number against the page. So the citation
chip opens a **page preview**: the cited page at reading size, with previous/next
paging and a jump-to-page control, served from
`GET /api/documents/{doc_id}/pages/{page}?dpi=150`. The renders table is keyed on
dpi, so a reading-size render caches alongside the model's 110 dpi one.

Page-level precision only. A VLM asked to return a bounding box for the number it
read will produce a confident and frequently wrong rectangle, which is the failure
this whole design exists to avoid — so the preview highlights nothing and simply
opens at the cited page. A coarse band (top/middle/third) is a possible refinement
once `extract_from_document` is calibrated, not a v1 promise.

The same preview opens from the attachment chip, so a document can be browsed
before or without any citation pointing into it.

## 6. Resolution policy

Fixed by measurement, not inferred per document:

| | 1-up | contact sheet |
| --- | --- | --- |
| default | 110 dpi | 4-up @62 dpi |
| floor | 45 dpi | never more than 4 per sheet |

5-up degraded even on sparse pages (13/16), and dense 4-up @45 failed badly (1/3).
Sparse documents are over-served at these settings by roughly 1.7× — 420 tokens a
page instead of 249. That is fractions of a cent, and it buys one less moving part
than a density heuristic would.

## 7. Budget

```python
# config.py
doc_image_budget: int = int(os.getenv("DOC_IMAGE_BUDGET", "120000"))
doc_page_dpi:     int = int(os.getenv("DOC_PAGE_DPI", "110"))
doc_sheet_dpi:    int = int(os.getenv("DOC_SHEET_DPI", "62"))
doc_sheet_cols:   int = int(os.getenv("DOC_SHEET_COLS", "2"))
```

120k is chosen against grok-4.5's 500k window and its price step at 200k: skill
(~5k) + canvas summary + conversation + tool traffic all have to fit under 200k
alongside the images for the turn to stay in the cheap tier.

At 420 tokens a page that is ~285 pages of contact sheets — so for most real
documents "turn 1 sees everything" is simply affordable. A 40-page report is ~17k.

## 8. Overflow

Three mechanisms, applied in order. None of them silently drops a page.

**Render-time planning.** `plan_sheets(page_count, budget)` returns the sheet set
that fits. If the whole document does not fit at 4-up @62, it steps the dpi down
toward the 45 floor. If it still does not fit, it renders the pages that do and
says so, in the label the model reads:

```
[northwind-fy25.pdf · 400 pages · showing contact sheets for pages 1-285
 (context budget) · call view_pages(doc, "286-400", mode="scan") for the rest]
```

**In-turn sliding window.** Page images must not accumulate across steps of the
ReAct loop. A `DocumentWindow` keeps images from the two most recent tool results
materialised and rewrites older ones to a stub:

```
[northwind-fy25.pdf p12 — viewed earlier this turn; call view_pages to look again]
```

Two steps of headroom means the model can still cross-reference the page it just
opened against the one before it, while the message list stays bounded no matter
how long the loop runs. `build_graph` takes the window so the `agent` node trims
immediately before `model.ainvoke`:

```python
async def agent(state: GraphState) -> dict[str, Any]:
    response = await model.ainvoke(window.trim(state["messages"]))
    return {"messages": [response]}
```

Trimming at the call and not in graph state matters: the untrimmed messages stay in
state, so `agent.py`'s stream bridge and the LangSmith trace still see the real
history.

**Cross-turn eviction.** No image survives a turn. `to_lc_messages` already replays
only text, so this is the existing behaviour rather than new machinery. What carries
forward is the digest — a few hundred tokens in the system message, placed *before*
the canvas summary so the stable prefix stays cacheable (xAI cached input is
$0.30/M against $2.00/M).

## 9. Summarising for later turns

The digest is written by the model, at the end of the first turn that views a
document, via `record_document_digest(docId, thesis, sections[])`. Model-authored
because it is the only participant that has actually read the pages — a parser
would be guessing, which is the thing this design exists to avoid.

Enforced the way `set_layout` already is. `TurnFlags` gains `viewed_docs` and
`digested`; in `run_turn`'s `finally` block, mirroring the existing
`normalize_rows` fallback:

```python
if turn.viewed_docs and not turn.digested:
    await backfill_digest(canvas_id, turn.viewed_docs)
```

The backfill is one cheap sub-model call over the contact sheets. A turn that
forgets still leaves a usable document behind.

## 10. Tools

Three, shaped like `web_search` so nothing new has to be explained to the model.
They are bound only when the canvas actually has a document — `build_tools` already
takes `canvas_id`, so a canvas with no uploads never sees `view_pages` in its schema
and cannot hallucinate a call to it.

- `view_pages(doc, pages, mode)` — `doc` is the filename (§2). `mode="scan"`
  returns contact sheets, `mode="read"` returns 1-up at reading dpi. `pages`
  accepts `"12"`, `"12-18"`, `"12,15,20"`, or `"all"`.
- `extract_from_document(doc, pages, question)` — the document twin of
  `run_search`. A sub-model call over the page images returns structured facts
  through `validate_fact`, stored by `record_facts(tool="document")`. This is the
  only path by which a number from a PDF reaches a widget, so the existing
  "measured needs a fact behind it" rejection covers uploads with no new rule.
- `record_document_digest(docId, thesis, sections)` — §9.

## 11. Privacy and data security

Uploads change this app's threat model more than any other feature in it. Until now
everything on a canvas came from public web search; now a user hands over a file that
may be a payslip, a contract or a medical record, and every page of it is transmitted
to a third party as an image.

**Every page reaches xAI. This is the architecture, not a leak** — but it must be
stated at the point of upload, not buried in a policy. The composer says which model
will read the file before the file is sent. Set `store_messages=False` on document
calls as `search.py:71` already does for search; xAI's own image guidance advises
against server-side history retention when sending images.

**Tracing is a second copy, and by default it is the whole payload.** `LANGSMITH_TRACING`
puts the full message list — including base64 page images — into LangSmith, so an
uploaded document lands in a second vendor and the traces become enormous.
`LANGSMITH_HIDE_INPUTS=true` solves it bluntly by destroying the trace's usefulness.
Better is a callable `hide_inputs` on the client that strips `image_url` blocks and
keeps their labels, so a trace reads:

```
[northwind-fy25.pdf p12]  <image redacted, 935x1210, 1387 tok>
```

Debuggability survives, the document does not leave. Also raise the floor in
`requirements.txt` from `langsmith>=0.1` — redaction could be bypassed by streaming
events before Python SDK 0.7.31 (CVE-2026-41182), and `>=0.1` permits those versions.

**There is no authentication anywhere in this service.** Every actor is
`'local-user'`. `GET /api/documents/{doc_id}/pages/{page}` would serve any page of any
document to anyone who can reach the port, with only a UUID as a secret. That is
acceptable on a laptop and unacceptable the moment the port is exposed — so bind to
localhost, and treat authentication as a hard prerequisite for deployment rather than
a later hardening task.

**Deletion is incomplete by default.** `ON DELETE CASCADE` clears
`documents.files` and `documents.renders`, but MinIO objects are not foreign keys and
will survive their canvas. Deleting a canvas or a document must explicitly remove the
object prefix, or the bytes outlive the record — a storage leak and an erasure
problem at once. Audit ingest, view and delete into `audit.events` alongside the
existing `record_facts` entries.

**Document content is untrusted input to an agent with tools.** A PDF can render
text that addresses the model — an instruction printed on a page is indistinguishable
from an instruction in the prompt once both are pixels. The existing agent boundary
contains most of the blast radius: the model cannot write storage, run shell, or emit
renderer code, only call typed operations the command layer validates. The residual
risks are an induced fabricated canvas, and exfiltration through the one outbound
channel the model controls — `web_search` query text. Mitigation is framing, not
filtering: the skill states that document pages are quoted material, never
instructions, and the `[filename p12]` label marks where the untrusted region begins.

**At rest and in transit.** MinIO defaults to neither encrypted nor TLS. Enable both
before this runs anywhere but a laptop.

**Retention is currently forever.** Documents live as long as their canvas because
nothing expires them. That is a decision to make deliberately, not a default to
inherit.

**What images-only improves.** No extracted text means no document prose in logs, no
text index to exfiltrate, and a persistent artifact — the digest — that is a
model-authored summary rather than verbatim content. The most sensitive representation
of the file exists only in MinIO and transiently in a request body.

## 12. Access control

Access is granted on the canvas; documents inherit it. Sharing a canvas that cites
documents requires an explicit decision about each one, and excluding a document
degrades its citations visibly rather than silently.

```sql
CREATE TABLE canvas.grants (
  canvas_id  UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  principal  TEXT NOT NULL,        -- 'user:<sub>' | 'group:<idp-group-id>'
  role       TEXT NOT NULL,        -- owner | editor | viewer
  granted_by TEXT NOT NULL,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (canvas_id, principal)
);

ALTER TABLE documents.files
  ADD COLUMN uploaded_by  TEXT NOT NULL,
  ADD COLUMN share_scope  TEXT NOT NULL DEFAULT 'canvas';  -- canvas | uploader
```

Canvas access is always a precondition — `share_scope` can only narrow it, never widen:

```python
def can_read_document(doc, user) -> bool:
    if not can_read_canvas(doc.canvas_id, user):
        return False
    return doc.share_scope == "canvas" or doc.uploaded_by == user.sub
```

**The consent gate.** `GET /api/canvases/{id}/share-preview` returns each document
and how many facts on the canvas rest on it, so the decision is made with the
disclosure in view. `POST /api/canvases/{id}/grants` then requires an explicit
`documents: {<filename>: "canvas" | "uploader"}` map and rejects the request if any
cited document is unaccounted for. Silence is never consent.

**Excluded documents degrade loudly.** A citation whose document the viewer cannot
read renders as unverifiable and says so — "source restricted by uploader" — rather
than 404-ing from a chip that looks live. This matters because the numbers are still
on the canvas: excluding a document withholds the evidence, not the finding, and the
UI must not imply otherwise.

**The share decision is an audit record.** Who shared, with whom, which documents
were included and which withheld, into `audit.events`. That record is the artifact
that answers "who authorised this disclosure" — the reason the gate exists at all.

## 13. Deployment hardening

§11 states the risks; this is what to build against them. Nothing here is specific to
one identity provider or one cloud.

### Identity: a backend-for-frontend, not a token in the browser

Authorization Code with PKCE, initiated and completed **server-side**. Tokens live in
a server-side session store; the browser holds only an opaque
`HttpOnly; Secure; SameSite=Lax` cookie. Refresh happens server-side.

Sessions are a Postgres table (`auth.sessions`) rather than Redis: one fewer service,
durable across a restart, and it reuses the existing pool. Swap for Redis when this
runs as more than one instance — the interface is three functions in `auth.py`.
Login state (PKCE verifier, nonce, `state`) needs no storage at all; it rides in a
signed cookie that expires in ten minutes.

This is not only a security preference — it is the only shape that works here.
`EventSource` (`web/src/App.tsx:59`) and `<img src>` cannot carry an `Authorization`
header, and page images are `<img src>`. A bearer-token SPA would force every page
image through a blob fetch and a rewrite of the event stream. A cookie authenticates
all three transports unchanged, and an XSS bug cannot read it.

Use plain OIDC (`authlib` server-side, `oidc-client-ts` in the client) so the provider
is an issuer URL in config. Enterprises change IdP more often than expected, usually
during an acquisition.

**Resolve groups to a role once, at session creation, and store the role in the
session.** Never re-derive permissions from a token claim per request: claims go stale
between refreshes, and a user removed from a group should lose access at their next
request, not their next login.

**Return 404, not 403, on an authorization miss.** A 403 confirms the resource exists.

Consider Postgres RLS over `canvas.grants` as defence in depth once §12 lands — the
blast radius of one missed guard is somebody else's documents.

### Ingest is the widest attack surface

Accepting files means parsing hostile input with native code.

- **Scan before parse.** ClamAV sidecar; `status='scanning'` until clean.
- **Sandbox the rasteriser.** Ingest runs as a separate worker with no network egress,
  read-only root filesystem, memory cap and wall-clock timeout, so a file that pops
  pypdfium2 lands somewhere that cannot reach Postgres or the internet.
- **Bound the inputs.** Max bytes, max page count, per-user upload rate. A
  5,000-page PDF is a denial-of-service against the render budget in §7.

### Egress is governed, not just configured

The controls in §11 become defaults rather than options:

- Trace redaction (`hide_inputs` stripping `image_url`) is **on by default**, tracing
  opt-in per organisation, `langsmith>=0.7.31`.
- `store_messages=False` on every xAI call.
- **Every egress is an audit event** — document, pages, model, model version, user,
  timestamp. Not for debugging: so "where did this file go" is answerable about a
  specific document during an incident.
- Obtain a zero-retention commitment from the model provider in writing, and record
  which model version saw which document.

### Erasure has to be a job

Postgres `ON DELETE CASCADE` does not touch MinIO. A `documents.deletions` queue with
retry, a reconciliation sweep for orphaned prefixes, and an audit entry on completion.
Per-tenant SSE-KMS keys make key destruction a cryptographic erasure, which is how a
deletion SLA is met without chasing objects through backups.

### Detect injection; do not pretend to filter it

§11 explains why framing is the only real mitigation. The detection half: **log every
`web_search` query issued during a turn that read a document**, and alert on length or
entropy anomalies. `web_search` is the single outbound channel the model controls, so
it is the only exfiltration path worth instrumenting.

### Sequencing

Identity, grants and deny-by-default land **before** upload ships. Retrofitting
tenancy onto stored documents is a data migration performed under audit rather than a
schema decision made once. Scanning, sandboxing and the erasure job land with upload.
Per-tenant keys and RLS can follow.

## 14. Scope

In: PDF, MinIO, ingest, contact sheets, the three tools, the budget and window, and
the page preview (§5) — the preview is not optional polish, it is what makes the
provenance claim true.

Out, deliberately: pptx/docx/xlsx, which need office → PDF rendering (LibreOffice
headless, ~500 MB) before they can join an images-only pipeline. Routing them
through a text converter instead would reintroduce exactly the failure this design
removes, so they wait for the render path rather than getting an exception.

Also out: search across documents too large to fit as contact sheets. Past ~285
pages the model pages through by hand. The answer when that bites is embeddings
over page images, not a text index.

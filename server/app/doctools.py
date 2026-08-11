"""The three document tools, shaped like `web_search` so nothing new has to be
explained to the model.

They are built only when the canvas actually has a readable document — a canvas with
no uploads never sees `view_pages` in its schema and so cannot call it.

`view_pages` returns a `ToolMessage` whose content is a list of text-then-image
blocks rather than a dict. Two reasons: `ToolNode` stringifies dict returns, which
would turn a page image into the literal text of a base64 URL; and xAI accepts image
content inside a tool result, which was verified before this was built, so no
synthetic follow-up turn is needed to smuggle the pixels in.
"""

import base64
import json
import logging
from typing import Annotated, Any, Literal

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langchain_xai import ChatXAI
from pydantic import BaseModel, Field

from . import ingest
from .config import settings
from .db import record_facts
from .docstore import set_digest
from .documents import DocumentDigest, RenderedImage, SectionNote, group_pages, parse_pages
from .specs import validate_fact

log = logging.getLogger("vision.doctools")

# One call must not consume the whole turn's image budget; the model can always ask
# for more pages in a following step, and the window will evict the earlier ones.
MAX_PAGES_PER_CALL = 8


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def image_blocks(rendered: list[tuple[bytes, RenderedImage]]) -> list[dict[str, Any]]:
    """Text label first, then the image it names.

    The label is read as a caption for what follows and is the exact string the model
    is asked to cite. A contact sheet without its stated reading order is uncitable —
    the model can see four pages but cannot say which is which.
    """
    blocks: list[dict[str, Any]] = []
    for data, meta in rendered:
        blocks.append({"type": "text", "text": meta.label()})
        blocks.append({"type": "image_url", "image_url": {"url": _data_url(data)}})
    return blocks


EXTRACT_INSTRUCTION = (
    "Read the attached page images and answer the question by extracting figures. "
    "Return ONLY a JSON object — no prose, no code fences — shaped:\n"
    '{"brief": "<one short factual paragraph>",\n'
    ' "facts": [\n'
    '   {"entity": "<the subject>", "label": "<what it measures>",\n'
    '    "unit": "<unit or null>", "asOf": "<YYYY-MM-DD or period>",\n'
    '    "value": <number for a single figure, else null>,\n'
    '    "points": [{"x": "<date/category>", "y": <number>}] | null,\n'
    '    "snippet": "<the exact words on the page the number was read from>",\n'
    '    "page": <the page number the figure appears on>,\n'
    '    "confidence": "measured"}\n'
    " ]}\n"
    "Every number a reader might chart must appear as a fact with its page. "
    "Read values only from what is visibly printed; if a figure is illegible, omit it "
    "rather than guessing. Question: "
)


def is_empty(fact: dict[str, Any]) -> bool:
    """A fact carrying no number at all. Nothing can bind to it and nothing should."""
    return fact.get("value") is None and not fact.get("points")


def _extract_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        s = s[nl + 1 :] if nl != -1 else s
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start : end + 1]
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001 — a malformed reply degrades, never crashes the turn
        return {}


DIGEST_INSTRUCTION = (
    "These are contact sheets of an entire document. Return ONLY a JSON object — no "
    "prose, no code fences — shaped:\n"
    '{"thesis": "<one sentence: what this document is and what it shows>",\n'
    ' "sections": [{"pages": "12-18", "what": "<what is on those pages>"}]}\n'
    "The sections are a map someone will navigate by without seeing the pages again, "
    "so cover the whole document and give real page ranges."
)


async def backfill_digest(doc: dict[str, Any]) -> DocumentDigest | None:
    """Summarise a document the model read but forgot to write down.

    Mirrors the `normalize_rows` fallback for a skipped `set_layout`: the turn still
    leaves a usable document behind. One cheap sub-model call over the contact sheets.
    """
    sheets, _ = await ingest.contact_sheets(doc)
    if not sheets:
        return None
    content = image_blocks(sheets)
    content.append({"type": "text", "text": DIGEST_INSTRUCTION})
    model = ChatXAI(model=settings.search_model, timeout=600)
    reply = await model.ainvoke([HumanMessage(content=content)])
    raw = (
        reply.content
        if isinstance(reply.content, str)
        else "".join(b.get("text", "") for b in reply.content if isinstance(b, dict))
    )
    data = _extract_json(raw)
    if not data.get("thesis"):
        return None
    sections = []
    for s in data.get("sections") or []:
        try:
            sections.append(SectionNote.model_validate(s))
        except Exception:  # noqa: BLE001 — a bad section is not worth losing the digest
            continue
    digest = DocumentDigest(
        filename=doc["filename"],
        pageCount=doc["page_count"],
        thesis=str(data["thesis"])[:400],
        sections=sections,
    )
    await set_digest(doc["id"], digest)
    log.info("backfilled digest for %s (%d sections)", doc["filename"], len(sections))
    return digest


class ViewPagesInput(BaseModel):
    doc: str = Field(description="The document's filename, exactly as it appears in the label.")
    pages: str = Field(
        description='Pages to look at: "12", "12-18", "12,15,20", or "all".'
    )
    mode: Literal["read", "scan"] = Field(
        default="read",
        description=(
            "'read' returns one page per image at full resolution — use it to read "
            "numbers. 'scan' tiles four pages per image to find your way around; do "
            "not read a figure off a scan."
        ),
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class DigestInput(BaseModel):
    doc: str = Field(description="The document's filename.")
    thesis: str = Field(description="One sentence: what this document is and what it shows.")
    sections: list[SectionNote] = Field(
        default_factory=list,
        description="A short map — page ranges and what is on them. This is all you "
        "will have of the document on later turns, so make it navigable.",
    )


class ExtractInput(BaseModel):
    doc: str = Field(description="The document's filename.")
    pages: str = Field(description='Pages to extract from: "12", "12-18", "all".')
    question: str = Field(description="What to extract, e.g. 'quarterly freight cost'.")


def build_document_tools(
    canvas_id: str,
    documents: list[dict[str, Any]],
    turn: Any,
    on_change,
    actor: str = "local-user",
) -> list[StructuredTool]:
    ready = {d["filename"]: d for d in documents if d["status"] == "ready"}
    if not ready:
        return []

    names = ", ".join(f'"{n}"' for n in ready)

    def resolve(name: str) -> dict[str, Any]:
        doc = ready.get(name)
        if doc is None:
            raise ValueError(f"no document named {name!r} on this canvas. Attached: {names}")
        return doc

    async def run_view_pages(**kwargs: Any) -> ToolMessage:
        args = ViewPagesInput.model_validate(kwargs)
        call_id = args.tool_call_id
        try:
            doc = resolve(args.doc)
            wanted = parse_pages(args.pages, doc["page_count"])
        except ValueError as e:
            return ToolMessage(content=str(e), tool_call_id=call_id, name="view_pages")

        if args.mode == "scan":
            groups = group_pages(wanted, settings.doc_sheet_cols * 2)
            rendered = [await ingest.sheet_image(doc, g) for g in groups]
        else:
            capped = wanted[:MAX_PAGES_PER_CALL]
            rendered = [await ingest.page_image(doc, p) for p in capped]
            if len(wanted) > MAX_PAGES_PER_CALL:
                rendered_note = (
                    f" (showing the first {MAX_PAGES_PER_CALL} of {len(wanted)} pages; "
                    "ask again for the rest)"
                )
            else:
                rendered_note = ""

        turn.viewed_docs.add(doc["filename"])
        metas = [m for _, m in rendered]
        await ingest.record_egress(doc, metas, actor)

        blocks = image_blocks(rendered)
        if args.mode == "read" and len(wanted) > MAX_PAGES_PER_CALL:
            blocks.insert(0, {"type": "text", "text": rendered_note.strip()})
        log.info(
            "view_pages %s %s (%s): %d images, %d tokens",
            doc["filename"],
            args.pages,
            args.mode,
            len(metas),
            sum(m.tokens for m in metas),
        )
        return ToolMessage(content=blocks, tool_call_id=call_id, name="view_pages")

    async def run_record_digest(**kwargs: Any) -> dict[str, Any]:
        args = DigestInput.model_validate(kwargs)
        try:
            doc = resolve(args.doc)
        except ValueError as e:
            return {"ok": False, "errors": [str(e)]}
        digest = DocumentDigest(
            filename=doc["filename"],
            pageCount=doc["page_count"],
            thesis=args.thesis,
            sections=args.sections,
        )
        await set_digest(doc["id"], digest)
        turn.digested.add(doc["filename"])
        on_change()
        return {"ok": True, "filename": doc["filename"], "sections": len(args.sections)}

    async def run_extract(**kwargs: Any) -> dict[str, Any]:
        args = ExtractInput.model_validate(kwargs)
        try:
            doc = resolve(args.doc)
            wanted = parse_pages(args.pages, doc["page_count"])[:MAX_PAGES_PER_CALL]
        except ValueError as e:
            return {"ok": False, "errors": [str(e)]}

        rendered = [await ingest.page_image(doc, p) for p in wanted]
        await ingest.record_egress(doc, [m for _, m in rendered], actor)
        turn.viewed_docs.add(doc["filename"])

        content = image_blocks(rendered)
        content.append({"type": "text", "text": EXTRACT_INSTRUCTION + args.question})
        model = ChatXAI(model=settings.search_model, timeout=600)
        reply = await model.ainvoke([HumanMessage(content=content)])

        raw = reply.content if isinstance(reply.content, str) else "".join(
            b.get("text", "") for b in reply.content if isinstance(b, dict)
        )
        data = _extract_json(raw)

        facts: list[dict[str, Any]] = []
        for f in data.get("facts") or []:
            page = f.pop("page", None) or wanted[0]
            f["sourceUrl"] = f"doc://{doc['id']}#p{page}"
            parsed, error = validate_fact(f)
            if parsed is None:
                log.debug("dropping malformed document fact: %s (%s)", f, error)
                continue
            if is_empty(parsed):
                # Observed in practice: asked for a segment's revenue, the extractor
                # emits {"label": "revenue"} with the FY24 and FY25 figures split into
                # sibling facts and nothing left in this one. `validate_fact` permits
                # a valueless fact by design, but one labelled "measured" with neither
                # a value nor points could be bound to a widget and render as null.
                log.debug("dropping valueless document fact: %s", parsed.get("label"))
                continue
            facts.append(parsed)

        stored = (
            await record_facts(canvas_id, facts, tool="document", query=args.question)
            if facts
            else []
        )
        on_change()
        return {
            "ok": True,
            "summary": data.get("brief") or raw[:600],
            "facts": [
                {
                    "factId": s["factId"],
                    "entity": s.get("entity"),
                    "label": s.get("label"),
                    "value": s.get("value"),
                    "unit": s.get("unit"),
                    "source": s.get("source_url"),
                }
                for s in stored
            ],
        }

    return [
        StructuredTool.from_function(
            coroutine=run_view_pages,
            name="view_pages",
            description=(
                "Look at pages of an attached document. They are images, so read them "
                "as you would read paper. Use mode='scan' to find your way around a "
                "document and mode='read' to read figures off a page — never take a "
                "number from a scan. Cite what you read as [filename p12]. "
                f"Attached: {names}."
            ),
            args_schema=ViewPagesInput,
        ),
        StructuredTool.from_function(
            coroutine=run_extract,
            name="extract_from_document",
            description=(
                "Pull structured, citable figures out of specific pages. Returns facts "
                "with a factId each, carrying the page they came from — bind measured "
                "numbers to those factIds instead of typing them. Use this rather than "
                "reading a number yourself when it is going onto the canvas."
            ),
            args_schema=ExtractInput,
        ),
        StructuredTool.from_function(
            coroutine=run_record_digest,
            name="record_document_digest",
            description=(
                "Write down what a document is and where things are in it, once, after "
                "you have looked through it. On later turns this map is ALL you will "
                "have of the document until you call view_pages again — so make it "
                "navigable, with page ranges for anything you might need to revisit."
            ),
            args_schema=DigestInput,
        ),
    ]

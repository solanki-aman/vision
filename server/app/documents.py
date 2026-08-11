"""Documents as pixels.

An uploaded file reaches the model as page images and nothing else — no text
extractor sits between the paper and the canvas. That is a correctness decision,
not a stylistic one: text extraction fails silently. MarkItDown's pdfplumber path
shifted every value of a bordered table one column right, so FY25 revenue read as
FY24, with a snippet and a source attached to make it look sound. The same table
read off a page image transcribed exactly. A silent column shift defeats the
provenance rules in `specs.py`, because the wrong number still arrives with
lineage. Pixels remove the failure mode rather than mitigating it.

This module is pure: it opens a PDF, measures it, and produces image bytes. It
knows nothing about MinIO, Postgres or the agent, so it is testable offline.

## The token model

Measured against grok-4.5 across seven image sizes, `prompt_tokens` fits

    tokens ~= 256 + (pixels / 1000)

within about 1%. It is linear in area with **no cap** — the widely repeated
"448x448 tiles, 1792 maximum" is not in xAI's documentation and is contradicted by
measurement (1275x1650 measured 2328 tokens). Budgets are computed from this, so
cost is known before a request is built rather than discovered from an API error.

## Resolution

Also measured, against ground truth. A sparse page at 36 dpi produced confident
wrong digits (450.0 for 455.0, -6.7% for -2.1%); at 45 dpi it was exact. Dense 9pt
pages need ~62 dpi in a 4-up and are safe at 110 solo. Five pages to a sheet
degraded even on sparse pages. Hence the defaults in `config.py` and the floor
here: resolution is a fixed policy, because images fail silently below it too.
"""

from __future__ import annotations

import asyncio
import io
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Literal

import pypdfium2 as pdfium
from PIL import Image
from pydantic import Field

from .specs import Spec

# pdfium is NOT thread-safe. Handing renders to `asyncio.to_thread` puts them on
# arbitrary pool threads, and a background ingest overlapping a `view_pages` call is
# then two concurrent pdfium calls — which segfaults the worker with no traceback,
# exactly as observed. One dedicated thread keeps every call serialised while still
# keeping CPU-bound rasterising off the event loop.
_RENDER_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdfium")


async def in_render_thread(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a pdfium call on the one thread allowed to touch pdfium."""
    return await asyncio.get_running_loop().run_in_executor(_RENDER_POOL, fn, *args)

# A tile is 448x448 at 256 tokens, but the effective rate measured end to end is
# closer to one token per 1000 pixels plus a fixed preamble. Trust the measurement.
TOKENS_BASE = 256
PIXELS_PER_TOKEN = 1000

# Below this a page image starts producing plausible wrong digits rather than
# refusing to be read, which is the one failure mode this design cannot tolerate.
DPI_FLOOR = 45

# 5-up degraded on sparse pages (13/16 cells) where 4-up was exact.
MAX_PAGES_PER_SHEET = 4


def estimate_tokens(width: int, height: int) -> int:
    """What an image of this size will cost, before it is built."""
    return TOKENS_BASE + (width * height) // PIXELS_PER_TOKEN


# ---- types ---------------------------------------------------------------------
# The filename is the model-facing handle; docId is internal. The model reads
# "[northwind-fy25.pdf p12]" on a label, writes the same string in prose, and passes
# the same string to view_pages — one identifier end to end, nothing to transpose.


class DocumentRef(Spec):
    """How a document is named everywhere: tool args, citations, UI."""

    docId: str
    filename: str
    pageCount: int
    mediaType: str


class PageRef(Spec):
    """The unit of citation. There is no second way to spell a page reference."""

    filename: str
    page: int = Field(ge=1, description="1-based — what a reader sees on the page.")

    def cite(self) -> str:
        return f"{self.filename} p{self.page}"


class RenderedImage(Spec):
    """Image bytes plus what they will cost. `pages` has one entry for a 1-up."""

    kind: Literal["page", "sheet"]
    filename: str
    pages: list[int]
    cols: int
    dpi: int
    width: int
    height: int
    tokens: int
    media_type: str = "image/png"

    def label(self) -> str:
        """The text block that precedes the image, and the string to cite.

        A contact sheet is uncitable without its reading order — the model can see
        four pages but cannot name which is which.
        """
        if self.kind == "page":
            return f"[{self.filename} p{self.pages[0]}]"
        rows = math.ceil(len(self.pages) / self.cols)
        if len(self.pages) == 1:
            return f"[{self.filename} p{self.pages[0]}]"
        spots = _grid_spots(len(self.pages), self.cols, rows)
        order = ", ".join(f"p{p} {spot}" for p, spot in zip(self.pages, spots))
        span = f"pages {self.pages[0]}-{self.pages[-1]}"
        return f"[{self.filename} {span} · {self.cols}x{rows} grid · reading order: {order}]"


class SectionNote(Spec):
    pages: str = Field(description='A page or range, e.g. "12-18".')
    what: str = Field(description="What is on those pages, in a few words.")


class DocumentDigest(Spec):
    """What survives into later turns. Model-authored: it is the only participant
    that actually read the pages, and a parser would be guessing."""

    filename: str
    pageCount: int
    thesis: str = Field(description="One sentence: what this document is.")
    sections: list[SectionNote] = Field(default_factory=list)

    def render(self) -> str:
        lines = [f"{self.filename} · {self.pageCount} pages", f"thesis: {self.thesis}"]
        lines += [f"  {s.pages:<8} {s.what}" for s in self.sections]
        return "\n".join(lines)


class SheetPlan(Spec):
    """Which pages go on which sheet, at what resolution, within a token budget."""

    dpi: int
    cols: int
    sheets: list[list[int]]
    tokens: int
    covered: int
    truncated_from: int | None = Field(
        default=None,
        description="First page the budget could not cover; None when the whole document fits.",
    )


_SPOTS_2 = ["left", "right"]
_SPOTS_ROWS = ["top", "middle", "bottom"]


def _grid_spots(count: int, cols: int, rows: int) -> list[str]:
    """Human names for grid positions, so a label can say which page is where."""
    out: list[str] = []
    for i in range(count):
        r, c = divmod(i, cols)
        if rows == 1:
            out.append(_SPOTS_2[c] if cols == 2 else f"position {i + 1}")
            continue
        vertical = _SPOTS_ROWS[r] if rows <= 3 else f"row {r + 1}"
        if rows == 2:
            vertical = "top" if r == 0 else "bottom"
        horizontal = _SPOTS_2[c] if cols == 2 else f"col {c + 1}"
        out.append(f"{vertical}-{horizontal}")
    return out


# ---- pdf ------------------------------------------------------------------------


def open_pdf(data: bytes) -> pdfium.PdfDocument:
    return pdfium.PdfDocument(data)


def page_count(data: bytes) -> int:
    doc = open_pdf(data)
    try:
        return len(doc)
    finally:
        doc.close()


def page_size_pt(data: bytes) -> tuple[float, float]:
    """First page dimensions in points, used to size sheets before rendering."""
    doc = open_pdf(data)
    try:
        page = doc[0]
        return float(page.get_width()), float(page.get_height())
    finally:
        doc.close()


def _to_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _render_one(doc: pdfium.PdfDocument, page_index: int, dpi: int) -> Image.Image:
    return doc[page_index].render(scale=dpi / 72).to_pil().convert("RGB")


def _check_pages(doc: pdfium.PdfDocument, pages: list[int], filename: str) -> None:
    """A page number reaches here straight from the model, which will eventually ask
    for page 500 of a 40-page document. Say so plainly — pdfium's own failure is an
    opaque "Failed to load page." that tells the model nothing it can act on."""
    total = len(doc)
    bad = [p for p in pages if p < 1 or p > total]
    if bad:
        listed = ", ".join(str(p) for p in bad)
        raise ValueError(f"{filename} has {total} pages; no page {listed}")


def render_page(data: bytes, filename: str, page: int, dpi: int) -> tuple[bytes, RenderedImage]:
    """One page at reading resolution. `page` is 1-based."""
    doc = open_pdf(data)
    try:
        _check_pages(doc, [page], filename)
        image = _render_one(doc, page - 1, dpi)
    finally:
        doc.close()
    meta = RenderedImage(
        kind="page",
        filename=filename,
        pages=[page],
        cols=1,
        dpi=dpi,
        width=image.width,
        height=image.height,
        tokens=estimate_tokens(image.width, image.height),
    )
    return _to_png(image), meta


# Each tile on a contact sheet gets its page number printed above it. The label
# already states the reading order, but that still asks the model to map a grid
# position onto a number — and it gets it wrong: an early run produced a digest
# placing the freight chart on p3 when it is on p4. Stamping the number into the
# pixels removes the inference. Costs about 40 tokens a sheet.
STAMP_BAND = 18
STAMP_BG = (238, 238, 238)
STAMP_FG = (40, 40, 40)


def _stamp(tile: Image.Image, page: int) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    out = Image.new("RGB", (tile.width, tile.height + STAMP_BAND), STAMP_BG)
    out.paste(tile, (0, STAMP_BAND))
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.load_default(size=13)
    except TypeError:  # Pillow < 10.1 has no size argument
        font = ImageFont.load_default()
    draw.text((4, 3), f"p{page}", fill=STAMP_FG, font=font)
    return out


def render_sheet(
    data: bytes, filename: str, pages: list[int], cols: int, dpi: int
) -> tuple[bytes, RenderedImage]:
    """Several pages tiled into one image, each stamped with its page number."""
    if not pages:
        raise ValueError("a sheet needs at least one page")
    if len(pages) > MAX_PAGES_PER_SHEET:
        raise ValueError(
            f"{len(pages)} pages per sheet exceeds the measured limit of {MAX_PAGES_PER_SHEET}"
        )
    doc = open_pdf(data)
    try:
        _check_pages(doc, pages, filename)
        tiles = [_stamp(_render_one(doc, p - 1, dpi), p) for p in pages]
    finally:
        doc.close()

    cell_w = max(t.width for t in tiles)
    cell_h = max(t.height for t in tiles)
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), "white")
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet.paste(tile, (c * cell_w, r * cell_h))

    meta = RenderedImage(
        kind="sheet",
        filename=filename,
        pages=list(pages),
        cols=cols,
        dpi=dpi,
        width=sheet.width,
        height=sheet.height,
        tokens=estimate_tokens(sheet.width, sheet.height),
    )
    return _to_png(sheet), meta


def parse_pages(spec: str, total: int) -> list[int]:
    """Turn a model-supplied page spec into page numbers.

    Accepts "12", "12-18", "12,15,20", "all", and combinations. Raises with the real
    page count on anything out of range, because the model guesses page numbers from
    a digest and needs to be told when it guessed wrong.
    """
    text = (spec or "").strip().lower()
    if not text:
        raise ValueError("no pages requested")
    if text == "all":
        return list(range(1, total + 1))

    pages: list[int] = []
    for part in text.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo_s, _, hi_s = chunk.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError as e:
                raise ValueError(f"{chunk!r} is not a page range") from e
            if lo > hi:
                lo, hi = hi, lo
            pages.extend(range(lo, hi + 1))
        else:
            try:
                pages.append(int(chunk))
            except ValueError as e:
                raise ValueError(f"{chunk!r} is not a page number") from e

    unique = sorted(set(pages))
    bad = [p for p in unique if p < 1 or p > total]
    if bad:
        listed = ", ".join(str(p) for p in bad)
        raise ValueError(f"document has {total} pages; no page {listed}")
    return unique


def group_pages(pages: list[int], per_sheet: int) -> list[list[int]]:
    """Chunk pages for contact sheets, never exceeding the measured 4-up limit."""
    size = min(per_sheet, MAX_PAGES_PER_SHEET)
    return [pages[i : i + size] for i in range(0, len(pages), size)]


def sheet_pixels(page_pt: tuple[float, float], cols: int, rows: int, dpi: int) -> tuple[int, int]:
    """Sheet dimensions, including the page-number band above every tile.

    The band is part of what gets rendered, so leaving it out of the estimate makes
    the budget under-count — harmless in the middle, wrong at the truncation
    boundary, where it would plan more sheets than actually fit.
    """
    scale = dpi / 72
    return (
        round(page_pt[0] * scale * cols),
        round((page_pt[1] * scale + STAMP_BAND) * rows),
    )


def plan_sheets(
    pages: int,
    page_pt: tuple[float, float],
    budget: int,
    *,
    cols: int = 2,
    dpi: int = 62,
    floor: int = DPI_FLOOR,
) -> SheetPlan:
    """Fit contact sheets for `pages` into `budget` tokens.

    Steps resolution down toward the floor before giving up, and when even the floor
    will not fit the whole document it covers what it can and reports where it
    stopped — the caller puts that in the label so the model is told, rather than
    silently handed a truncated document.
    """
    per_sheet = min(cols * cols, MAX_PAGES_PER_SHEET)
    rows = math.ceil(per_sheet / cols)

    def groups(limit: int) -> list[list[int]]:
        out = []
        for start in range(1, limit + 1, per_sheet):
            out.append(list(range(start, min(start + per_sheet, limit + 1))))
        return out

    for d in range(dpi, floor - 1, -1):
        w, h = sheet_pixels(page_pt, cols, rows, d)
        each = estimate_tokens(w, h)
        total = math.ceil(pages / per_sheet) * each
        if total <= budget:
            sheets = groups(pages)
            return SheetPlan(
                dpi=d, cols=cols, sheets=sheets, tokens=total, covered=pages, truncated_from=None
            )

    # Even at the floor the whole document will not fit: cover what the budget allows.
    w, h = sheet_pixels(page_pt, cols, rows, floor)
    each = estimate_tokens(w, h)
    n_sheets = max(1, budget // each)
    covered = min(pages, n_sheets * per_sheet)
    sheets = groups(covered)
    return SheetPlan(
        dpi=floor,
        cols=cols,
        sheets=sheets,
        tokens=len(sheets) * each,
        covered=covered,
        truncated_from=None if covered >= pages else covered + 1,
    )

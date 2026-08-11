"""The rendering core: token estimation, sheet planning, and image geometry.

The numbers asserted here are not arbitrary — they come from measuring grok-4.5's
reported `prompt_tokens` across seven image sizes and from transcription accuracy
against known table values. If these change, the cost model and the resolution
floor in `app/documents.py` are both wrong, so they are worth pinning.
"""

import math

import pypdfium2 as pdfium
import pytest

from app.documents import (
    DPI_FLOOR,
    MAX_PAGES_PER_SHEET,
    DocumentDigest,
    PageRef,
    SectionNote,
    estimate_tokens,
    page_count,
    page_size_pt,
    plan_sheets,
    render_page,
    render_sheet,
)

LETTER = (612.0, 792.0)


def make_pdf(pages: int, size: tuple[float, float] = LETTER) -> bytes:
    """A blank N-page PDF. Geometry is all these tests need, so no content."""
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(size[0], size[1])
    buf = __import__("io").BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---- the token model -------------------------------------------------------------

# (width, height, tokens reported by grok-4.5). The measured values include a short
# text prompt alongside the image, so the model is expected to track within a few
# percent rather than exactly.
MEASURED = [
    (306, 396, 382),
    (383, 495, 444),
    (468, 605, 537),
    (612, 792, 752),
    (935, 1210, 1392),
    (1054, 1364, 1671),
    (1275, 1650, 2328),
]


@pytest.mark.parametrize("width,height,observed", MEASURED)
def test_token_estimate_tracks_measured_prompt_tokens(width, height, observed):
    predicted = estimate_tokens(width, height)
    assert abs(predicted - observed) / observed < 0.03, (
        f"{width}x{height}: predicted {predicted}, measured {observed}"
    )


def test_tokens_scale_with_area_and_are_not_capped():
    """The '1792 token maximum' repeated by third-party docs does not exist —
    a 1275x1650 page measured 2328. Designing around a cap would under-budget."""
    assert estimate_tokens(1275, 1650) > 1792
    assert estimate_tokens(2000, 2000) > estimate_tokens(1000, 1000) * 3


# ---- pdf geometry ----------------------------------------------------------------


def test_page_count_and_size():
    data = make_pdf(5)
    assert page_count(data) == 5
    w, h = page_size_pt(data)
    assert (round(w), round(h)) == (612, 792)


@pytest.mark.parametrize("dpi,expected", [(45, (383, 495)), (62, (527, 682)), (110, (935, 1210))])
def test_render_page_dimensions_match_the_dpi_policy(dpi, expected):
    """These are the sizes the accuracy measurements were taken at."""
    _, meta = render_page(make_pdf(1), "x.pdf", 1, dpi)
    assert (meta.width, meta.height) == expected
    assert meta.tokens == estimate_tokens(*expected)


def test_render_page_returns_png_bytes():
    data, _ = render_page(make_pdf(1), "x.pdf", 1, 45)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_sheet_tiles_four_pages_into_a_2x2_grid():
    from app.documents import STAMP_BAND

    _, meta = render_sheet(make_pdf(4), "x.pdf", [1, 2, 3, 4], cols=2, dpi=62)
    assert meta.pages == [1, 2, 3, 4]
    # Each tile carries a page-number band above it, so the grid is two cells wide
    # and two cells tall where a cell is a page plus its stamp.
    assert (meta.width, meta.height) == (527 * 2, (682 + STAMP_BAND) * 2)
    # The contact-sheet cost the whole budget is planned around. The stamp adds ~40.
    assert meta.tokens == pytest.approx(1731, abs=15)


def test_contact_sheet_tiles_are_stamped_with_their_page_number():
    """Without this the model infers page numbers from grid position, and it gets it
    wrong — an early run placed a chart on p3 that is on p4."""
    from PIL import Image
    import io as _io

    data, meta = render_sheet(make_pdf(4), "x.pdf", [1, 2, 3, 4], cols=2, dpi=62)
    sheet = Image.open(_io.BytesIO(data))
    # The stamp band sits above each tile and is not page content.
    top_left = sheet.crop((0, 0, 60, 16)).convert("L")
    assert min(top_left.getdata()) < 120, "expected dark stamp text in the band"


def test_render_sheet_refuses_more_than_four_pages():
    """5-up measured 13/16 table cells correct where 4-up was exact. The limit is
    a correctness boundary, so it is enforced rather than documented."""
    with pytest.raises(ValueError, match="exceeds the measured limit"):
        render_sheet(make_pdf(6), "x.pdf", [1, 2, 3, 4, 5], cols=2, dpi=62)


# ---- labels ----------------------------------------------------------------------


def test_page_label_is_the_citation_string():
    _, meta = render_page(make_pdf(12), "northwind-fy25.pdf", 12, 110)
    assert meta.label() == "[northwind-fy25.pdf p12]"
    assert PageRef(filename="northwind-fy25.pdf", page=12).cite() == "northwind-fy25.pdf p12"


def test_sheet_label_states_reading_order_so_the_grid_is_citable():
    _, meta = render_sheet(make_pdf(8), "r.pdf", [5, 6, 7, 8], cols=2, dpi=62)
    label = meta.label()
    assert "pages 5-8" in label
    assert "2x2 grid" in label
    for page, spot in [(5, "top-left"), (6, "top-right"), (7, "bottom-left"), (8, "bottom-right")]:
        assert f"p{page} {spot}" in label


def test_page_numbers_are_one_based():
    with pytest.raises(Exception):
        PageRef(filename="x.pdf", page=0)


@pytest.mark.parametrize("page", [0, 6, 500])
def test_out_of_range_pages_say_so_instead_of_failing_opaquely(page):
    """The model supplies these, and it will eventually ask for a page that does not
    exist. pdfium's own error is 'Failed to load page.', which it cannot act on."""
    with pytest.raises(ValueError, match=r"has 5 pages; no page"):
        render_page(make_pdf(5), "r.pdf", page, 110)


def test_out_of_range_pages_are_caught_for_sheets_too():
    with pytest.raises(ValueError, match=r"has 4 pages; no page 9"):
        render_sheet(make_pdf(4), "r.pdf", [1, 9], cols=2, dpi=62)


# ---- planning --------------------------------------------------------------------


def test_a_forty_page_document_fits_whole_at_full_resolution():
    plan = plan_sheets(40, LETTER, budget=120_000)
    assert plan.dpi == 62
    assert plan.truncated_from is None
    assert plan.covered == 40
    assert len(plan.sheets) == 10
    assert plan.sheets[0] == [1, 2, 3, 4]
    assert plan.sheets[-1] == [37, 38, 39, 40]
    assert plan.tokens == pytest.approx(17_310, rel=0.01)


def test_the_plan_matches_what_actually_gets_rendered():
    """The planner and the renderer have to agree on size, or the budget is fiction.

    They disagreed once: `sheet_pixels` did not account for the page-number band the
    renderer adds, so every sheet came out 38 tokens larger than planned. Harmless in
    the middle of a budget, wrong at the truncation boundary.
    """
    plan = plan_sheets(4, LETTER, budget=120_000)
    _, meta = render_sheet(make_pdf(4), "x.pdf", plan.sheets[0], cols=plan.cols, dpi=plan.dpi)
    assert meta.tokens == pytest.approx(plan.tokens / len(plan.sheets), rel=0.005)


def test_a_tight_budget_steps_resolution_down_before_giving_up():
    full = plan_sheets(40, LETTER, budget=120_000)
    tight = plan_sheets(40, LETTER, budget=14_000)
    assert tight.dpi < full.dpi
    assert tight.dpi >= DPI_FLOOR
    assert tight.truncated_from is None
    assert tight.covered == 40


def test_resolution_never_drops_below_the_floor():
    """Below the floor the model returns confident wrong digits, so truncating the
    document is the correct failure — not rendering it illegibly."""
    plan = plan_sheets(4000, LETTER, budget=5_000)
    assert plan.dpi == DPI_FLOOR
    assert plan.truncated_from is not None


def test_truncation_is_reported_rather_than_silent():
    plan = plan_sheets(4000, LETTER, budget=20_000)
    assert plan.covered < 4000
    assert plan.truncated_from == plan.covered + 1
    assert plan.sheets[-1][-1] == plan.covered
    assert plan.tokens <= 20_000


def test_a_partial_last_sheet_holds_only_real_pages():
    plan = plan_sheets(6, LETTER, budget=120_000)
    assert plan.sheets == [[1, 2, 3, 4], [5, 6]]
    assert plan.covered == 6


def test_pages_per_sheet_stays_within_the_measured_limit():
    plan = plan_sheets(100, LETTER, budget=120_000)
    assert all(len(s) <= MAX_PAGES_PER_SHEET for s in plan.sheets)
    assert sum(len(s) for s in plan.sheets) == plan.covered


def test_plan_covers_every_page_exactly_once():
    plan = plan_sheets(37, LETTER, budget=120_000)
    seen = [p for sheet in plan.sheets for p in sheet]
    assert seen == list(range(1, 38))


# ---- digest ----------------------------------------------------------------------


def test_digest_renders_as_the_compact_block_carried_between_turns():
    digest = DocumentDigest(
        filename="northwind-fy25.pdf",
        pageCount=40,
        thesis="revenue grew 16.6% but freight rose 147.9%",
        sections=[SectionNote(pages="1-4", what="cover, executive summary")],
    )
    text = digest.render()
    assert "northwind-fy25.pdf · 40 pages" in text
    assert "thesis: revenue grew" in text
    assert "1-4" in text and "cover, executive summary" in text
    # It has to stay small enough to sit in every later turn's system message.
    assert len(text) < 1200


def test_digest_is_far_cheaper_than_the_sheets_it_replaces():
    plan = plan_sheets(40, LETTER, budget=120_000)
    digest = DocumentDigest(
        filename="northwind-fy25.pdf",
        pageCount=40,
        thesis="x" * 80,
        sections=[SectionNote(pages=f"{i}", what="y" * 30) for i in range(6)],
    )
    assert len(digest.render()) // 4 < plan.tokens / 20


# ---- thread safety -------------------------------------------------------------------


def test_all_pdfium_work_runs_on_one_thread():
    """pdfium is not thread-safe. Concurrent calls killed the worker process outright
    — no exception, no traceback, just a dead child — so rendering is pinned to a
    single dedicated thread. This asserts that pinning, because the failure it
    prevents is invisible in logs."""
    import asyncio

    from app.documents import in_render_thread

    def thread_name(_: int) -> str:
        import threading

        return threading.current_thread().name

    async def run() -> set[str]:
        names = await asyncio.gather(*(in_render_thread(thread_name, i) for i in range(12)))
        return set(names)

    used = asyncio.run(run())
    assert len(used) == 1, f"pdfium work spread across {used}"
    assert next(iter(used)).startswith("pdfium")


def test_concurrent_renders_are_serialised_and_all_succeed():
    import asyncio

    from app.documents import in_render_thread

    data = make_pdf(4)

    async def run():
        jobs = [in_render_thread(render_page, data, "x.pdf", (i % 4) + 1, 45) for i in range(8)]
        jobs += [in_render_thread(render_sheet, data, "x.pdf", [1, 2, 3, 4], 2, 45) for _ in range(4)]
        return await asyncio.gather(*jobs)

    results = asyncio.run(run())
    assert len(results) == 12
    assert all(png[:8] == b"\x89PNG\r\n\x1a\n" for png, _ in results)

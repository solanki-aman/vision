"""Fact binding — the rule that the model does not get to type a number.

The command layer resolves each bound value from its stored fact and refuses any
widget that claims "measured" while some number in it has nothing behind it.
These tests pin that contract down without needing a database: the only thing
`bind_and_materialize` asks of its connection is a `fetch`.
"""

import pytest

from app.commands import (
    _covered,
    _get_at,
    _norm_path,
    _numeric_leaves,
    _set_at,
    bind_and_materialize,
)

CANVAS = "11111111-1111-1111-1111-111111111111"
F_SCALAR = "22222222-2222-2222-2222-222222222222"
F_SERIES = "33333333-3333-3333-3333-333333333333"


class FakeConn:
    """Stands in for asyncpg: bind_and_materialize only ever calls fetch()."""

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, _query, _canvas_id, ids):
        wanted = {str(i) for i in ids}
        return [r for r in self.rows if str(r["id"]) in wanted]


def conn_with_facts():
    return FakeConn(
        [
            {"id": F_SCALAR, "kind": "scalar", "value": 81615.0, "points": None},
            {
                "id": F_SERIES,
                "kind": "series",
                "value": None,
                "points": [{"x": "Q1", "y": 1.0}, {"x": "Q2", "y": 2.0}],
            },
        ]
    )


MEASURED = {"source": "s", "confidence": "measured"}
ILLUSTRATIVE = {"source": "s", "confidence": "illustrative"}


# ---- path helpers ---------------------------------------------------------------

@pytest.mark.parametrize(
    "path,expected",
    [
        ("value", ["value"]),
        ("comparison.baseline", ["comparison", "baseline"]),
        ("series.0", ["series", "0"]),
        ("series[0].data", ["series", "0", "data"]),
        ("lines.3.value", ["lines", "3", "value"]),
    ],
)
def test_norm_path_accepts_dotted_and_bracketed(path, expected):
    assert _norm_path(path) == expected


def test_get_and_set_walk_lists_and_dicts():
    spec = {"lines": [{"value": 1}, {"value": 2}]}
    assert _get_at(spec, ["lines", "1", "value"]) == 2
    _set_at(spec, ["lines", "1", "value"], 99)
    assert spec["lines"][1]["value"] == 99


def test_get_at_returns_none_for_a_path_that_does_not_exist():
    assert _get_at({"a": 1}, ["b", "c"]) is None


def test_covered_matches_a_leaf_under_a_bound_prefix():
    # Binding a whole series covers every point inside it.
    assert _covered(["series", "0", "data", "7"], [["series", "0"]])
    assert not _covered(["series", "1", "data", "0"], [["series", "0"]])


# ---- which numbers actually need a source ---------------------------------------

def test_numeric_leaves_finds_kpi_value_baseline_and_sparkline():
    leaves = _numeric_leaves(
        "kpi",
        {"value": 5.0, "comparison": {"baseline": 4.0}, "sparkline": [1.0, 2.0]},
    )
    assert ["value"] in leaves
    assert ["comparison", "baseline"] in leaves
    assert ["sparkline", "0"] in leaves


def test_numeric_leaves_covers_chart_series_data():
    leaves = _numeric_leaves("chart", {"series": [{"name": "a", "data": [1.0, 2.0]}]})
    assert leaves == [["series", "0", "data", "0"], ["series", "0", "data", "1"]]


def test_numeric_leaves_ignores_editorial_and_derived_numbers():
    # Annotation values are marks the author places, not measurements; statement
    # `percent` is a share the UI computes. Neither should demand a fact.
    chart = _numeric_leaves(
        "chart", {"series": [], "annotations": [{"kind": "reference_line", "value": 10.0}]}
    )
    assert chart == []
    stmt = _numeric_leaves("statement", {"lines": [{"label": "x", "value": 1.0, "percent": 50.0}]})
    assert stmt == [["lines", "0", "value"]]


def test_numeric_leaves_skips_non_numeric_table_cells():
    leaves = _numeric_leaves(
        "table", {"rows": [{"name": "Lam", "revenue": 18435.0, "note": None}]}
    )
    assert leaves == [["rows", "0", "revenue"]]


# ---- the binding contract -------------------------------------------------------

async def test_bound_value_is_overwritten_by_the_fact():
    """The model's typed number is a hint at most — the fact is authoritative."""
    spec = {"value": 999.0, "label": "revenue"}
    out, err = await bind_and_materialize(
        conn_with_facts(), CANVAS, "kpi", spec,
        [{"path": "value", "factId": F_SCALAR}], MEASURED,
    )
    assert err is None
    assert out["value"] == 81615.0


async def test_series_binding_fills_data_and_axis_from_the_fact():
    spec = {"chartType": "line", "series": [{"name": "close", "data": []}]}
    out, err = await bind_and_materialize(
        conn_with_facts(), CANVAS, "chart", spec,
        [{"path": "series.0", "factId": F_SERIES}], MEASURED,
    )
    assert err is None
    assert out["series"][0]["data"] == [1.0, 2.0]
    assert out["xAxis"]["categories"] == ["Q1", "Q2"]


async def test_measured_number_without_a_binding_is_rejected():
    spec = {"value": 42.0, "label": "invented"}
    out, err = await bind_and_materialize(
        conn_with_facts(), CANVAS, "kpi", spec, [], MEASURED
    )
    assert out is None
    assert "value" in err and "not bound" in err


async def test_illustrative_widgets_bypass_binding():
    """An invented shape is honest when it says so — it just may not say 'measured'."""
    spec = {"value": 42.0, "label": "a shape, not a measurement"}
    out, err = await bind_and_materialize(
        conn_with_facts(), CANVAS, "kpi", spec, [], ILLUSTRATIVE
    )
    assert err is None
    assert out["value"] == 42.0


async def test_binding_to_an_unknown_fact_is_rejected():
    out, err = await bind_and_materialize(
        conn_with_facts(), CANVAS, "kpi", {"value": 1.0},
        [{"path": "value", "factId": "44444444-4444-4444-4444-444444444444"}], MEASURED,
    )
    assert out is None
    assert "not a fact on this canvas" in err


async def test_binding_to_a_path_that_does_not_exist_is_rejected():
    out, err = await bind_and_materialize(
        conn_with_facts(), CANVAS, "kpi", {"value": 1.0},
        [{"path": "nope.missing", "factId": F_SCALAR}], MEASURED,
    )
    assert out is None
    assert "does not resolve" in err


async def test_binding_needs_both_a_path_and_a_fact():
    out, err = await bind_and_materialize(
        conn_with_facts(), CANVAS, "kpi", {"value": 1.0}, [{"path": "value"}], MEASURED
    )
    assert out is None
    assert "needs a path and a factId" in err

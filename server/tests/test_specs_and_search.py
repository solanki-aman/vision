"""Spec/fact validation and the search extractor's tolerance for messy replies."""

import pytest

from app.search import _extract_json
from app.specs import validate_fact, validate_spec


# ---- facts ----------------------------------------------------------------------

def test_scalar_fact_round_trips():
    fact, err = validate_fact(
        {
            "kind": "scalar",
            "entity": "NVDA",
            "label": "Q1 FY2027 revenue",
            "unit": "USD millions",
            "asOf": "2026-04-26",
            "value": 81615.0,
            "snippet": "…revenue of $81.6 billion…",
            "sourceUrl": "https://nvidianews.nvidia.com/x",
            "confidence": "measured",
        }
    )
    assert err is None
    assert fact["value"] == 81615.0 and fact["entity"] == "NVDA"


def test_series_fact_keeps_its_points():
    fact, err = validate_fact(
        {"kind": "series", "label": "daily close", "points": [{"x": "2026-01-01", "y": 1.5}]}
    )
    assert err is None
    assert fact["points"] == [{"x": "2026-01-01", "y": 1.5}]


def test_fact_defaults_to_a_measured_scalar():
    fact, _ = validate_fact({"label": "bare"})
    assert fact["kind"] == "scalar" and fact["confidence"] == "measured"


def test_fact_without_a_label_is_rejected():
    fact, err = validate_fact({"value": 1.0})
    assert fact is None and "label" in err


def test_unknown_fact_fields_are_rejected():
    # extra="forbid" keeps the model from smuggling unmodelled data into storage.
    fact, err = validate_fact({"label": "x", "sneaky": 1})
    assert fact is None and "sneaky" in err


# ---- widget specs ---------------------------------------------------------------

def test_valid_kpi_spec_passes():
    spec, err = validate_spec("kpi", {"value": 1.0, "label": "revenue"})
    assert err is None and spec["value"] == 1.0


def test_unknown_widget_kind_is_rejected():
    spec, err = validate_spec("hologram", {})
    assert spec is None and "unknown widget kind" in err


def test_chart_spec_rejects_an_unknown_chart_type():
    spec, err = validate_spec("chart", {"chartType": "spiral"})
    assert spec is None and "chartType" in err


# ---- search extraction ----------------------------------------------------------

def test_extracts_a_plain_json_object():
    assert _extract_json('{"brief": "hi", "facts": []}')["brief"] == "hi"


def test_extracts_json_from_a_fenced_block():
    raw = '```json\n{"brief": "fenced", "facts": []}\n```'
    assert _extract_json(raw)["brief"] == "fenced"


def test_extracts_json_surrounded_by_prose():
    raw = 'Sure! Here you go:\n{"brief": "wrapped"}\nHope that helps.'
    assert _extract_json(raw)["brief"] == "wrapped"


@pytest.mark.parametrize("raw", ["", "no json here", "{not valid json"])
def test_unparseable_replies_degrade_to_empty_rather_than_raising(raw):
    # A malformed search reply must not take the whole turn down; the caller
    # falls back to the raw text as the brief and simply stores no facts.
    assert _extract_json(raw) == {}

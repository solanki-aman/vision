"""Settings resolution.

docker compose passes an unset variable through as an EMPTY STRING (`${FOO:-}`),
and os.getenv reports that as set — so a getenv default never fires and an empty
model name reaches the API as `INVALID_ARGUMENT: Model not found: ""`. These pin
the fallback behaviour that prevents it.
"""

import importlib

import pytest

import app.config


def reload_settings(monkeypatch, env: dict[str, str | None]):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return importlib.reload(app.config).settings


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(app.config)


def test_empty_model_falls_back_instead_of_being_used(monkeypatch):
    s = reload_settings(monkeypatch, {"XAI_MODEL": ""})
    assert s.xai_model == "grok-4.5"


def test_empty_search_model_falls_back_to_the_main_model(monkeypatch):
    s = reload_settings(
        monkeypatch, {"XAI_MODEL": "grok-4.5", "XAI_SEARCH_MODEL": ""}
    )
    assert s.search_model == "grok-4.5"


def test_search_model_is_used_when_actually_set(monkeypatch):
    s = reload_settings(
        monkeypatch, {"XAI_MODEL": "grok-4.5", "XAI_SEARCH_MODEL": "grok-4.1-fast"}
    )
    assert s.search_model == "grok-4.1-fast"


def test_empty_reasoning_effort_falls_back(monkeypatch):
    s = reload_settings(monkeypatch, {"XAI_REASONING_EFFORT": ""})
    assert s.xai_reasoning_effort == "low"


def test_reasoning_effort_is_respected_when_set(monkeypatch):
    s = reload_settings(monkeypatch, {"XAI_REASONING_EFFORT": "medium"})
    assert s.xai_reasoning_effort == "medium"


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("yes", True), ("", False), ("false", False)])
def test_tracing_flag_parsing(monkeypatch, value, expected):
    s = reload_settings(monkeypatch, {"LANGSMITH_TRACING": value})
    assert s.langsmith_tracing is expected

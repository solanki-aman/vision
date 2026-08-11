"""The restricted evaluator behind code_execution.

These guard the two things that matter: that the sandbox actually refuses the
obvious escapes, and that the `result` contract holds — a derived fact is only
honest if the number in it came from the code we show the reader.
"""

import math

import pytest

from app.compute import run_compute


def test_returns_the_result_variable():
    assert run_compute("result = 2 + 3", {}) == 5


def test_inputs_are_bound_as_variables():
    code = "growth = (q1 - q4) / q4 * 100\nresult = round(growth, 1)"
    assert run_compute(code, {"q1": 81615.0, "q4": 68127.0}) == pytest.approx(19.8)


def test_series_input_and_list_result():
    code = "result = [v * 2 for v in closes]"
    assert run_compute(code, {"closes": [1.0, 2.0, 3.0]}) == [2.0, 4.0, 6.0]


def test_math_helpers_are_available():
    assert run_compute("result = sqrt(16)", {}) == 4.0
    assert run_compute("result = round(log10(1000))", {}) == 3


def test_comments_do_not_break_execution():
    # The skill asks the model to comment its code, since the code IS the explanation.
    code = "# Sum of the four quarters already on the canvas\ntotal = a + b\nresult = total"
    assert run_compute(code, {"a": 1.5, "b": 2.5}) == 4.0


def test_missing_result_is_an_error():
    with pytest.raises(ValueError, match="result"):
        run_compute("answer = 42", {})


def test_runtime_failure_is_reported_not_raised_raw():
    with pytest.raises(ValueError, match="code failed"):
        run_compute("result = 1 / 0", {})


@pytest.mark.parametrize(
    "code",
    [
        "import os\nresult = 1",
        "result = __import__('os').listdir('.')",
        "result = open('/etc/passwd').read()",
        "result = eval('1+1')",
        "result = globals()",
        "result = (1).__class__.__mro__",
        "import subprocess\nresult = 1",
    ],
)
def test_escape_attempts_are_rejected(code):
    with pytest.raises(ValueError, match="disallowed token"):
        run_compute(code, {})


def test_builtins_not_on_the_allowlist_are_unavailable():
    # `exec` is stripped from the namespace even without tripping the token filter.
    with pytest.raises(ValueError, match="code failed"):
        run_compute("result = exec", {})


def test_input_names_do_not_leak_between_runs():
    run_compute("result = a", {"a": 1})
    with pytest.raises(ValueError, match="code failed"):
        run_compute("result = a", {})

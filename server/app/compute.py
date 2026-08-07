"""Local code execution for derived facts.

The model runs Python over named fact inputs; the value it produces becomes a fact
with `derived_from` lineage and the `formula` that made it. Doing arithmetic here
(rather than in the model's head) is both more correct and fully traceable.

This is a RESTRICTED evaluator, not a hardened sandbox: builtins are limited to a
safe arithmetic subset, imports and dunder access are rejected, and it runs inside
the server container over the user's own facts. Adequate for ratios, growth, sums
and reshaping series; not a place to run untrusted third-party code.
"""

import math
from typing import Any

_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "len": len,
    "pow": pow, "sorted": sorted, "range": range, "float": float, "int": int,
    "list": list, "tuple": tuple, "enumerate": enumerate, "zip": zip, "map": map,
    "filter": filter, "any": any, "all": all,
}
_SAFE_MATH: dict[str, Any] = {
    name: getattr(math, name)
    for name in ("sqrt", "log", "log10", "log2", "exp", "floor", "ceil", "pi", "e", "fabs")
}

# Defence in depth on top of the stripped builtins — reject obvious escapes early.
_BANNED = (
    "__", "import", "open(", "eval(", "exec(", "compile(", "globals(", "locals(",
    "getattr", "setattr", "delattr", "vars(", "input(", "breakpoint", "os.", "sys.",
    "subprocess", "socket", "pickle", "marshal", "builtins",
)


def run_compute(code: str, inputs: dict[str, Any]) -> Any:
    """Execute `code` with `inputs` as local variables; return its `result`."""
    for token in _BANNED:
        if token in code:
            raise ValueError(f"disallowed token in code: {token!r}")
    namespace: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        **_SAFE_MATH,
        **inputs,
    }
    try:
        exec(code, namespace)  # noqa: S102 — restricted namespace; see module docstring
    except Exception as e:  # noqa: BLE001 — report the failure to the model to fix
        raise ValueError(f"code failed: {e}") from e
    if "result" not in namespace:
        raise ValueError("the code must assign its answer to a variable named `result`")
    return namespace["result"]

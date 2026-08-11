"""Keep document page images out of LangSmith.

Tracing sends the whole message list, so once pages are in that list an uploaded
document lands in a second vendor as well as xAI — and the traces become enormous,
since one page is a few hundred kilobytes of base64.

`LANGSMITH_HIDE_INPUTS=true` solves it by destroying the trace's usefulness. This
strips only the image payloads and keeps their labels, so a trace still reads

    [northwind-fy25.pdf p12]  <image redacted, 935x1210>

which is everything needed to debug a turn and nothing of the document.

Redaction runs client-side, before the payload is serialised. Note the floor in
`requirements.txt`: before Python SDK 0.7.31 streaming events could bypass redaction
(CVE-2026-41182), which matters a great deal when redaction is what stands between a
user's payslip and a third party.
"""

import logging
import os
from typing import Any

log = logging.getLogger("vision.tracing")

REDACTED = "<image redacted>"


def scrub(value: Any) -> Any:
    """Recursively replace image_url payloads with a short placeholder."""
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            url = (value.get("image_url") or {}).get("url", "")
            kind = url.split(";", 1)[0].removeprefix("data:") if url.startswith("data:") else "url"
            return {"type": "image_url", "image_url": {"url": f"{REDACTED} {kind}"}}
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return REDACTED
    return value


def install() -> str:
    """Point LangChain's tracer at a client that scrubs inputs and outputs.

    Returns which strategy took effect, so startup can say so out loud rather than
    leaving it to be discovered in a trace.
    """
    if (os.getenv("LANGSMITH_TRACING", "").lower() not in ("1", "true", "yes")):
        return "off"
    if os.getenv("LANGSMITH_REDACT_IMAGES", "true").lower() in ("0", "false", "no"):
        log.warning(
            "LANGSMITH_REDACT_IMAGES is disabled — document page images will be sent "
            "to LangSmith in full."
        )
        return "disabled"

    try:
        from langsmith import Client
        from langchain_core.tracers import langchain as lc_tracer

        client = Client(hide_inputs=scrub, hide_outputs=scrub)
        # The tracer caches a module-level client; replace it before the first run.
        lc_tracer._CLIENT = client  # type: ignore[attr-defined]
        if hasattr(lc_tracer, "get_client"):
            lc_tracer.get_client = lambda: client  # type: ignore[assignment]
        return "client"
    except Exception:  # noqa: BLE001
        # The private hook moved. Fall back to the blunt documented switch rather
        # than silently shipping images: a useless trace beats a leaked document.
        log.exception("could not install the scrubbing LangSmith client")
        os.environ["LANGSMITH_HIDE_INPUTS"] = "true"
        os.environ["LANGSMITH_HIDE_OUTPUTS"] = "true"
        return "env-fallback"

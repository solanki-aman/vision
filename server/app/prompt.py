"""Loader for the viz-gen Agent Skill.

The methodology (art direction, storytelling, layout grammars, chart selection,
finance/people conventions, voice) lives in a standalone skill file so it is
versioned and edited independently of orchestration. `graph.py` composes the
system message as: skill body + current canvas summary + today's date.
"""

from functools import lru_cache
from pathlib import Path

SKILL_PATH = Path(__file__).parent / "skills" / "viz-gen" / "SKILL.md"


def _strip_frontmatter(text: str) -> str:
    """Drop the leading `--- ... ---` YAML block; keep the instruction body."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[text.find("\n", end + 1) + 1 :].lstrip("\n")
    return text


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return _strip_frontmatter(SKILL_PATH.read_text(encoding="utf-8")).strip()


# Backwards-compatible alias for existing imports.
SYSTEM_PROMPT = system_prompt()

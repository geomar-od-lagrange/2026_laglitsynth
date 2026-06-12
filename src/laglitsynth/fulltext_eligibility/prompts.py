"""Prompt construction for full-text eligibility assessment.

The system prompt is loaded from an external eligibility-criteria YAML
at runtime so swapping topics is configuration work rather than a
refactor. ``render_fulltext`` flattens a ``TeiDocument`` into a single
string the LLM can consume. ``build_user_message`` wraps the rendered
body for the user message.
"""

from __future__ import annotations

from laglitsynth.fulltext_extraction.tei import TeiDocument, flatten_sections
from laglitsynth.prompts import load_system_prompt

__all__ = [
    "USER_TEMPLATE",
    "build_user_message",
    "load_system_prompt",
    "render_fulltext",
]

USER_TEMPLATE = "full_text:\n{text}"


def render_fulltext(tei: TeiDocument) -> str:
    """Flatten TEI sections into a single blank-line-separated string.

    Depth-first walk of ``tei.sections()``; each section title + its
    paragraphs form one block, blocks separated by a blank line.
    Returns the empty string when ``sections()`` is empty; the caller
    treats an empty render as a ``tei-parse-failure`` since stage 7 has
    no abstract fallback.
    """
    return "\n\n".join(flatten_sections(tei))


def build_user_message(text: str) -> str:
    """Wrap rendered full-text body for the user message."""
    return USER_TEMPLATE.format(text=text)

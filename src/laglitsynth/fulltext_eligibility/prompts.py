"""Prompt construction for full-text eligibility assessment.

The system prompt is loaded from an external eligibility-criteria YAML
at runtime so swapping topics is configuration work rather than a
refactor. ``render_fulltext`` flattens a ``TeiDocument`` into a single
string the LLM can consume. ``build_user_message`` wraps the rendered
body with the ``source_basis`` tag the system prompt references.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laglitsynth.config import resolve_yaml_arg
from laglitsynth.fulltext_eligibility.models import SourceBasis
from laglitsynth.fulltext_extraction.tei import TeiDocument, flatten_sections

USER_TEMPLATE = "{source_basis}:\n{text}"


def load_system_prompt(spec: str | Path | dict[str, Any]) -> str:
    """Return the eligibility-criteria system prompt from a YAML spec.

    ``spec`` may be a path to a YAML file or an already-loaded mapping
    (the inlined-snapshot case). The mapping must carry a string-valued
    ``system_prompt`` field.
    """
    loaded = resolve_yaml_arg(spec)
    prompt = loaded.get("system_prompt")
    if not isinstance(prompt, str):
        raise ValueError(
            "eligibility-criteria spec must include a string 'system_prompt' field"
        )
    return prompt


def render_fulltext(tei: TeiDocument) -> str:
    """Flatten TEI sections into a single blank-line-separated string.

    Depth-first walk of ``tei.sections()``; each section title + its
    paragraphs form one block, blocks separated by a blank line.
    Returns the empty string when ``sections()`` is empty so the caller
    can fall back to the abstract.
    """
    return "\n\n".join(flatten_sections(tei))


def build_user_message(source_basis: SourceBasis, text: str) -> str:
    """Wrap rendered body text with the ``source_basis`` tag."""
    return USER_TEMPLATE.format(source_basis=source_basis, text=text)

"""Prompt construction for full-text eligibility assessment.

The system prompt is the canonical text of the three-point eligibility
criterion. ``render_fulltext`` flattens a ``TeiDocument`` into a single
string the LLM can consume. ``build_user_message`` wraps the rendered
body with the ``source_basis`` tag the system prompt references.
"""

from __future__ import annotations

from laglitsynth.fulltext_eligibility.models import SourceBasis
from laglitsynth.fulltext_extraction.tei import TeiDocument, flatten_sections

SYSTEM_PROMPT = """\
You are assessing whether a scientific paper meets the inclusion criteria
for a systematic review of numerical methods in Lagrangian oceanography.

Criteria:
1. The paper describes a computation that tracks particles, tracers, or
   objects in an ocean flow field.
2. The paper is primary research (not a review, editorial, or commentary).
3. The paper contains at least some description of the numerical methods
   used.

Respond with JSON: {"eligible": true|false, "reason": "<one sentence>"}.
Return ONLY the JSON object, nothing else."""

USER_TEMPLATE = "{source_basis}:\n{text}"


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

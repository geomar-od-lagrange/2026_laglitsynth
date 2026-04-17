"""Prompt construction for full-text eligibility assessment.

The system prompt is the canonical text of the three-point eligibility
criterion. ``render_fulltext`` flattens a ``TeiDocument`` into a single
string the LLM can consume; ``render_abstract`` is the thin fallback path
for works without an ``ExtractedDocument``. ``build_user_message`` wraps
either rendered body with the ``source_basis`` tag the system prompt
references.
"""

from __future__ import annotations

from laglitsynth.fulltext_eligibility.models import SourceBasis
from laglitsynth.fulltext_extraction.tei import Section, TeiDocument

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


def _flatten_section(section: Section) -> list[str]:
    """Depth-first flatten of a ``Section`` into title+paragraph blocks.

    Each section contributes one block: the title (if any) on its first
    line, followed by its paragraphs (one per line). Nested children
    contribute their own blocks in document order.
    """
    blocks: list[str] = []
    lines: list[str] = []
    if section.title:
        lines.append(section.title)
    lines.extend(section.paragraphs)
    if lines:
        blocks.append("\n".join(lines))
    for child in section.children:
        blocks.extend(_flatten_section(child))
    return blocks


def render_fulltext(tei: TeiDocument) -> str:
    """Flatten TEI sections into a single blank-line-separated string.

    Depth-first walk of ``tei.sections()``; each section title + its
    paragraphs form one block, blocks separated by a blank line.
    Returns the empty string when ``sections()`` is empty so the caller
    can fall back to the abstract.
    """
    blocks: list[str] = []
    for top in tei.sections():
        blocks.extend(_flatten_section(top))
    return "\n\n".join(blocks)


def render_abstract(abstract: str) -> str:
    """Return the abstract string unchanged; kept as a rendering seam."""
    return abstract


def build_user_message(source_basis: SourceBasis, text: str) -> str:
    """Wrap rendered body text with the ``source_basis`` tag."""
    return USER_TEMPLATE.format(source_basis=source_basis, text=text)
